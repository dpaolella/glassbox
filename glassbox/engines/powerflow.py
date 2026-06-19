"""Steady-state security: AC Newton-Raphson power flow + N-1, DC/PTDF.

PRD Section 6.5 — facets: pf, core. The Newton-Raphson iteration is built by
hand (numpy) so explain() can surface the admittance matrix, the power-mismatch
vector, the Jacobian, and the iteration trace (Section 2.3 / 6.1). The operating
point comes from a dispatch snapshot (the PCM -> power-flow handoff, Section 6.7):
an economically optimal, network-naive dispatch can produce flows that violate AC
limits the transport model never saw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..explain import ExplainPayload, Formulation
from ..schema import PowerFlowResult, Provenance, World
from .base import ENGINE_VERSION

HOURS_PER_YEAR = 8760


# --- admittance + branch model ----------------------------------------------


@dataclass
class Branch:
    id: str
    i: int            # from bus index
    j: int            # to bus index
    y_series: complex  # series admittance (pu)
    b_shunt: float     # total line charging (pu), split half each end
    tap: complex       # complex tap ratio (1 for lines)
    rating_mva: float
    rating_emergency_mva: float
    kind: str


@dataclass
class PFCase:
    n: int
    bus_ids: list[str]
    bus_index: dict[str, int]
    Ybus: np.ndarray
    branches: list[Branch]
    slack: int
    pv: list[int]
    pq: list[int]
    p_spec: np.ndarray      # pu, length n (gen - load)
    q_spec: np.ndarray      # pu
    v_set: np.ndarray       # |V| setpoint per bus (1.0 default; PV/slack hold)
    base_mva: float
    v_min: np.ndarray
    v_max: np.ndarray
    gen_p_mw: dict[str, float] = field(default_factory=dict)


def build_ybus(world: World) -> tuple[np.ndarray, list[Branch], dict[str, int]]:
    """Assemble the bus admittance matrix Y from lines, transformers, shunts."""
    bus_ids = [b.id for b in world.buses]
    idx = {b: k for k, b in enumerate(bus_ids)}
    n = len(bus_ids)
    Y = np.zeros((n, n), dtype=complex)
    branches: list[Branch] = []

    for ln in world.ac_lines:
        if ln.from_bus_id not in idx or ln.to_bus_id not in idx:
            continue
        i, j = idx[ln.from_bus_id], idx[ln.to_bus_id]
        y = 1.0 / complex(ln.r, ln.x)
        bsh = ln.b
        Y[i, i] += y + 1j * bsh / 2
        Y[j, j] += y + 1j * bsh / 2
        Y[i, j] -= y
        Y[j, i] -= y
        branches.append(Branch(ln.id, i, j, y, bsh, complex(1.0, 0.0),
                               ln.rating_normal_mva, ln.rating_emergency_mva, "ac_line"))

    for tr in world.transformers:
        if tr.from_bus_id not in idx or tr.to_bus_id not in idx:
            continue
        i, j = idx[tr.from_bus_id], idx[tr.to_bus_id]
        y = 1.0 / complex(tr.r, tr.x)
        t = tr.tap_ratio * np.exp(1j * np.deg2rad(tr.phase_shift_deg))
        # standard off-nominal tap model (tap on the 'from' side)
        Y[i, i] += y / (abs(t) ** 2)
        Y[j, j] += y
        Y[i, j] += -y / np.conj(t)
        Y[j, i] += -y / t
        branches.append(Branch(tr.id, i, j, y, 0.0, t, tr.rating_mva,
                               tr.rating_mva * 1.2, "transformer"))

    for sh in world.shunts:
        if sh.bus_id not in idx:
            continue
        Y[idx[sh.bus_id], idx[sh.bus_id]] += complex(sh.g, sh.b)

    return Y, branches, idx


# --- dispatch snapshot (the PCM -> power-flow handoff, Section 6.7) ----------


def peak_load_hour(world: World, weather_year: int) -> int:
    store = world.time_series_store
    sl = slice(weather_year * HOURS_PER_YEAR, (weather_year + 1) * HOURS_PER_YEAR)
    total = np.zeros(HOURS_PER_YEAR)
    for ld in world.loads:
        if ld.demand_profile_id and ld.demand_profile_id in store:
            total += store.get(ld.demand_profile_id)[sl]
    return int(total.argmax())


def snapshot_dispatch(world: World, hour: int, weather_year: int,
                      mode: str = "nodal") -> dict[str, float]:
    """Single-hour economic dispatch providing the AC power-flow operating point.

    This is the PCM -> power-flow handoff (Section 6.7). ``mode="nodal"`` solves a
    DC-network-feasible dispatch (deliverable, so AC power flow converges and
    reveals the losses the DC model omitted). ``mode="zonal"`` solves a transport
    dispatch that respects only aggregate inter-zonal limits — the AC power flow
    on the full nodal network then exposes intra-zonal overloads the transport
    model never saw (Section 6.5 phenomenon).
    """
    from ..operators import SpatialMode, SpatialProjection
    from .economic_core import (
        EngineOptions,
        assemble_view,
        build_dispatch_model,
        solve_model,
    )

    abs_h = weather_year * HOURS_PER_YEAR + hour
    sm = SpatialMode.AGGREGATE if mode == "zonal" else SpatialMode.IDENTITY
    sview = SpatialProjection(sm).apply(world)
    view = assemble_view(world, sview, np.array([abs_h]), np.array([0]),
                         np.array([1.0]), 1.0, investment=False)
    model = build_dispatch_model(view, EngineOptions(unit_commitment=False,
                                                     reserves=False, label="pf_snap"))
    solve_model(model)
    gp = model.m.variables["gen_p"].solution
    return {g.id: float(gp.sel(g=g.id).values[0]) for g in view.gens}


def assemble_pf_case(world: World, hour: int, weather_year: int,
                     dispatch: Optional[dict[str, float]] = None,
                     dispatch_mode: str = "nodal") -> PFCase:
    """Build a power-flow case at one hour from a dispatch snapshot."""
    Y, branches, idx = build_ybus(world)
    n = len(idx)
    bus_ids = [b.id for b in world.buses]
    store = world.time_series_store
    abs_h = weather_year * HOURS_PER_YEAR + hour

    if dispatch is None:
        dispatch = snapshot_dispatch(world, hour, weather_year, mode=dispatch_mode)

    base = world.base_power_mva
    p_spec = np.zeros(n)
    q_spec = np.zeros(n)
    v_set = np.ones(n)
    is_pv = [False] * n

    # generation injections
    gen_bus = {}
    for g in world.generators:
        if g.id in dispatch and g.bus_id in idx:
            b = idx[g.bus_id]
            p_spec[b] += dispatch[g.id] / base
            gen_bus.setdefault(b, []).append(g)
            if g.v_setpoint_pu and not g.is_vre and dispatch[g.id] > 0:
                is_pv[b] = True
                v_set[b] = g.v_setpoint_pu
    for h in world.hydro_units:
        if h.id in dispatch and h.bus_id in idx:
            b = idx[h.bus_id]
            p_spec[b] += dispatch[h.id] / base
            is_pv[b] = True  # synchronous machine regulates voltage

    # loads (P and Q from power factor)
    q_load_pu = np.zeros(n)
    for ld in world.loads:
        if ld.bus_id not in idx or not ld.demand_profile_id:
            continue
        if ld.demand_profile_id not in store:
            continue
        b = idx[ld.bus_id]
        pmw = float(store.get(ld.demand_profile_id)[abs_h])
        p_spec[b] -= pmw / base
        pf = min(max(ld.power_factor, 0.5), 1.0)
        q = pmw * np.tan(np.arccos(pf))
        q_spec[b] -= q / base
        q_load_pu[b] += q / base

    # Local VAR compensation (shunt capacitors at load buses, standard
    # power-factor correction). Without it the radial load center is reactive-
    # starved and AC power flow collapses; surfaced in explain() as an assumption.
    var_comp = 0.95
    for b in range(n):
        if q_load_pu[b] > 0:
            Y[b, b] += 1j * (var_comp * q_load_pu[b])

    # VRE/storage converters with reactive capability also regulate voltage
    for g in world.generators:
        if g.is_vre and g.id in dispatch and dispatch[g.id] > 0 and g.bus_id in idx:
            if g.q_max_mvar and g.q_max_mvar > 0:
                is_pv[idx[g.bus_id]] = True

    slack = idx.get(world.reference_bus_id, 0)
    is_pv[slack] = False
    pv = [k for k in range(n) if is_pv[k] and k != slack]
    pq = [k for k in range(n) if not is_pv[k] and k != slack]

    v_min = np.array([b.v_min_pu for b in world.buses])
    v_max = np.array([b.v_max_pu for b in world.buses])

    return PFCase(n=n, bus_ids=bus_ids, bus_index=idx, Ybus=Y, branches=branches,
                  slack=slack, pv=pv, pq=pq, p_spec=p_spec, q_spec=q_spec,
                  v_set=v_set, base_mva=base, v_min=v_min, v_max=v_max,
                  gen_p_mw=dict(dispatch))


# --- Newton-Raphson ---------------------------------------------------------


def _power_injection(V: np.ndarray, Y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    S = V * np.conj(Y @ V)
    return S.real, S.imag


def _build_jacobian(V: np.ndarray, theta: np.ndarray, Y: np.ndarray,
                    pv: list[int], pq: list[int]) -> np.ndarray:
    """Polar-form Jacobian [[H, N], [J, L]] over [Δθ_(pv+pq); Δ|V|_pq]."""
    n = len(V)
    G, B = Y.real, Y.imag
    Vm = np.abs(V)
    npvpq = pv + pq
    # full angle/voltage partials, then slice
    H = np.zeros((n, n))  # dP/dθ
    N = np.zeros((n, n))  # dP/dV
    Jm = np.zeros((n, n))  # dQ/dθ
    L = np.zeros((n, n))  # dQ/dV
    P, Q = _power_injection(V, Y)
    for i in range(n):
        for k in range(n):
            if i == k:
                H[i, i] = -Q[i] - B[i, i] * Vm[i] ** 2
                N[i, i] = P[i] / Vm[i] + G[i, i] * Vm[i]
                Jm[i, i] = P[i] - G[i, i] * Vm[i] ** 2
                L[i, i] = Q[i] / Vm[i] - B[i, i] * Vm[i]
            else:
                th = theta[i] - theta[k]
                gij, bij = G[i, k], B[i, k]
                H[i, k] = Vm[i] * Vm[k] * (gij * np.sin(th) - bij * np.cos(th))
                N[i, k] = Vm[i] * (gij * np.cos(th) + bij * np.sin(th))
                Jm[i, k] = -Vm[i] * Vm[k] * (gij * np.cos(th) + bij * np.sin(th))
                L[i, k] = Vm[i] * (gij * np.sin(th) - bij * np.cos(th))
    H11 = H[np.ix_(npvpq, npvpq)]
    N12 = N[np.ix_(npvpq, pq)]
    J21 = Jm[np.ix_(pq, npvpq)]
    L22 = L[np.ix_(pq, pq)]
    top = np.hstack([H11, N12])
    bot = np.hstack([J21, L22])
    return np.vstack([top, bot])


@dataclass
class PFSolution:
    converged: bool
    iterations: int
    V: np.ndarray
    theta: np.ndarray
    mismatch_trace: list[float]
    last_jacobian: Optional[np.ndarray] = None


def solve_newton_raphson(case: PFCase, tol: float = 1e-8, max_iter: int = 30) -> PFSolution:
    n = case.n
    Vm = np.array(case.v_set, dtype=float)
    Vm[case.pq] = 1.0
    theta = np.zeros(n)
    theta[case.slack] = 0.0
    Y = case.Ybus
    pv, pq, slack = case.pv, case.pq, case.slack
    npvpq = pv + pq

    trace: list[float] = []
    last_J = None
    converged = False
    it = 0
    for it in range(1, max_iter + 1):
        V = Vm * np.exp(1j * theta)
        P, Q = _power_injection(V, Y)
        dP = case.p_spec - P
        dQ = case.q_spec - Q
        mismatch = np.concatenate([dP[npvpq], dQ[pq]])
        max_mis = float(np.max(np.abs(mismatch))) if mismatch.size else 0.0
        trace.append(max_mis)
        if max_mis < tol:
            converged = True
            break
        # divergence guard: a network-naive dispatch can be AC-infeasible
        # (voltage collapse). Stop cleanly rather than emit garbage.
        if max_mis > 1e4 or np.any(Vm > 5.0) or np.any(Vm < 0.05):
            break
        J = _build_jacobian(V, theta, Y, pv, pq)
        last_J = J
        try:
            dx = np.linalg.solve(J, mismatch)
        except np.linalg.LinAlgError:
            break
        dtheta = dx[: len(npvpq)]
        dVm = dx[len(npvpq):]
        for a, b in enumerate(npvpq):
            theta[b] += dtheta[a]
        for a, b in enumerate(pq):
            Vm[b] += dVm[a]
    V = Vm * np.exp(1j * theta)
    return PFSolution(converged=converged, iterations=it, V=V, theta=theta,
                      mismatch_trace=trace, last_jacobian=last_J)


# --- branch flows + losses --------------------------------------------------


def branch_flows(case: PFCase, V: np.ndarray) -> dict[str, dict]:
    """Per-branch complex flow at the 'from' end and loss, in MVA/MW."""
    out = {}
    base = case.base_mva
    for br in case.branches:
        Vi, Vj = V[br.i], V[br.j]
        if br.kind == "transformer":
            t = br.tap
            Ii = (Vi / (abs(t) ** 2) - Vj / np.conj(t)) * br.y_series
        else:
            Ii = (Vi - Vj) * br.y_series + Vi * (1j * br.b_shunt / 2)
            Ij = (Vj - Vi) * br.y_series + Vj * (1j * br.b_shunt / 2)
        Sij = Vi * np.conj(Ii)
        if br.kind == "transformer":
            Ij = (Vj - Vi / np.conj(t)) * br.y_series
        Sji = Vj * np.conj(Ij)
        loss = Sij + Sji
        out[br.id] = {
            "p_from_mw": float(Sij.real) * base,
            "q_from_mvar": float(Sij.imag) * base,
            "s_from_mva": float(abs(Sij)) * base,
            "loss_mw": float(loss.real) * base,
            "rating_mva": br.rating_mva,
            "rating_emergency_mva": br.rating_emergency_mva,
            "loading_pct": float(abs(Sij)) * base / br.rating_mva * 100 if br.rating_mva else 0.0,
        }
    return out


# --- DC power flow + PTDF ----------------------------------------------------


def dc_powerflow(case: PFCase) -> dict[str, float]:
    """Linear DC power flow: B'θ = P (lossless). Returns per-branch MW flow."""
    n = case.n
    Bp = np.zeros((n, n))
    for br in case.branches:
        x = (1.0 / br.y_series).imag
        b = 1.0 / x if x else 0.0
        Bp[br.i, br.i] += b
        Bp[br.j, br.j] += b
        Bp[br.i, br.j] -= b
        Bp[br.j, br.i] -= b
    keep = [k for k in range(n) if k != case.slack]
    theta = np.zeros(n)
    theta[keep] = np.linalg.solve(Bp[np.ix_(keep, keep)], case.p_spec[keep])
    flows = {}
    for br in case.branches:
        x = (1.0 / br.y_series).imag
        flows[br.id] = float((theta[br.i] - theta[br.j]) / x) * case.base_mva if x else 0.0
    return flows


# --- N-1 contingency analysis -----------------------------------------------


def run_n1(world: World, case: PFCase) -> dict[str, list[dict]]:
    """Outage each element_outage disturbance, re-solve AC, flag violations."""
    violations: dict[str, list[dict]] = {}
    branch_by_id = {br.id: br for br in case.branches}
    for dist in world.disturbances:
        if dist.kind.value != "element_outage":
            continue
        outaged = [e for e in dist.affected_element_ids if e in branch_by_id]
        if not outaged:
            continue
        # rebuild a case with the outaged branch(es) removed
        sub = _case_without(case, outaged)
        sol = solve_newton_raphson(sub)
        v_list: list[dict] = []
        if not sol.converged:
            v_list.append({"type": "non_convergence", "element": dist.id})
        else:
            flows = branch_flows(sub, sol.V)
            for bid, f in flows.items():
                if f["s_from_mva"] > f["rating_emergency_mva"] + 1e-6:
                    v_list.append({"type": "thermal_overload", "branch": bid,
                                   "flow_mva": round(f["s_from_mva"], 1),
                                   "emergency_mva": f["rating_emergency_mva"],
                                   "loading_pct": round(
                                       f["s_from_mva"] / f["rating_emergency_mva"] * 100, 1)})
            Vm = np.abs(sol.V)
            for k in range(case.n):
                if Vm[k] < case.v_min[k] - 1e-3 or Vm[k] > case.v_max[k] + 1e-3:
                    v_list.append({"type": "voltage_violation", "bus": case.bus_ids[k],
                                   "v_pu": round(float(Vm[k]), 4)})
        if v_list:
            violations[dist.id] = v_list
    return violations


def _case_without(case: PFCase, outaged_ids: list[str]) -> PFCase:
    keep = [br for br in case.branches if br.id not in outaged_ids]
    n = case.n
    Y = np.zeros((n, n), dtype=complex)
    for br in keep:
        i, j = br.i, br.j
        if br.kind == "transformer":
            t = br.tap
            Y[i, i] += br.y_series / (abs(t) ** 2)
            Y[j, j] += br.y_series
            Y[i, j] += -br.y_series / np.conj(t)
            Y[j, i] += -br.y_series / t
        else:
            Y[i, i] += br.y_series + 1j * br.b_shunt / 2
            Y[j, j] += br.y_series + 1j * br.b_shunt / 2
            Y[i, j] -= br.y_series
            Y[j, i] -= br.y_series
    return PFCase(n=n, bus_ids=case.bus_ids, bus_index=case.bus_index, Ybus=Y,
                  branches=keep, slack=case.slack, pv=case.pv, pq=case.pq,
                  p_spec=case.p_spec, q_spec=case.q_spec, v_set=case.v_set,
                  base_mva=case.base_mva, v_min=case.v_min, v_max=case.v_max)


# --- engine ------------------------------------------------------------------


class PowerFlowEngine:
    facets = ["pf", "core"]
    name = "pf"

    def __init__(self, hour: Optional[int] = None, weather_year: int = 0,
                 run_contingencies: bool = True, dispatch_mode: str = "nodal"):
        self.hour = hour
        self.weather_year = weather_year
        self.run_contingencies = run_contingencies
        self.dispatch_mode = dispatch_mode

    def run(self, world: World) -> tuple[PowerFlowResult, ExplainPayload]:
        hour = self.hour if self.hour is not None else peak_load_hour(world, self.weather_year)
        case = assemble_pf_case(world, hour, self.weather_year,
                                dispatch_mode=self.dispatch_mode)
        sol = solve_newton_raphson(case)

        result = PowerFlowResult(engine="pf", engine_version=ENGINE_VERSION)
        result.converged = sol.converged
        result.iterations = sol.iterations
        result.convergence_trace = [float(x) for x in sol.mismatch_trace]

        Vm = np.abs(sol.V)
        ang = np.angle(sol.V, deg=True)
        result.bus_v_pu = {case.bus_ids[k]: round(float(Vm[k]), 5) for k in range(case.n)}
        result.bus_angle_deg = {case.bus_ids[k]: round(float(ang[k]), 4) for k in range(case.n)}

        flows = branch_flows(case, sol.V)
        result.branch_flow_mw = {b: round(f["p_from_mw"], 2) for b, f in flows.items()}
        result.branch_flow_mvar = {b: round(f["q_from_mvar"], 2) for b, f in flows.items()}
        result.losses_mw = round(sum(f["loss_mw"] for f in flows.values()), 3)

        if self.run_contingencies and sol.converged:
            result.contingency_violations = run_n1(world, case)

        result.provenance = Provenance(
            engine="pf", engine_version=ENGINE_VERSION,
            governing=["Y·V nodal admittance", "Newton-Raphson power-balance",
                       "branch thermal ratings", "bus voltage limits"],
            notes=f"operating point: copper-plate dispatch snapshot at hour {hour}")
        return result, self._explain(world, case, sol, result, flows, hour)

    def _explain(self, world, case, sol, result, flows, hour) -> ExplainPayload:
        dc = dc_powerflow(case)
        # base-case violations vs the network-naive dispatch
        base_overloads = {b: round(f["loading_pct"], 1) for b, f in flows.items()
                          if f["loading_pct"] > 100.0}
        # the lesson: DC ignores losses; AC reveals them
        dc_flow_sample = {b: round(dc.get(b, 0.0), 1) for b in list(flows)[:6]}
        ac_flow_sample = {b: round(flows[b]["p_from_mw"], 1) for b in list(flows)[:6]}
        return ExplainPayload(
            title="AC Power Flow (Newton-Raphson) + N-1 contingency screening",
            formulation=Formulation(
                statement=("Solve the nonlinear nodal power balance S = V·conj(Y·V) "
                           "by Newton-Raphson, then screen each N-1 outage. The "
                           "operating point is a network-naive economic dispatch, "
                           "so AC limits the transport model never saw can bind."),
                symbolic=[
                    "Y_bus from π-line, transformer-tap and shunt models",
                    "P_i = |V_i| Σ_k |V_k|(G_ik cosθ_ik + B_ik sinθ_ik)",
                    "Q_i = |V_i| Σ_k |V_k|(G_ik sinθ_ik − B_ik cosθ_ik)",
                    "[Δθ; Δ|V|] = J⁻¹ [ΔP; ΔQ],  J = [[H,N],[J,L]]",
                    "S_ij = V_i·conj(I_ij);  loss = Σ_branches (S_ij + S_ji)",
                ],
                variables=["θ at PV+PQ buses", "|V| at PQ buses"],
            ),
            inputs={
                "snapshot_hour": hour, "weather_year": self.weather_year,
                "n_buses": case.n, "n_branches": len(case.branches),
                "slack_bus": case.bus_ids[case.slack],
                "n_pv": len(case.pv), "n_pq": len(case.pq),
                "dispatch_mw": {k: round(v, 1) for k, v in case.gen_p_mw.items() if v > 0},
            },
            outputs={
                "converged": result.converged, "iterations": result.iterations,
                "total_losses_mw": result.losses_mw,
                "min_voltage_pu": round(float(np.abs(sol.V).min()), 4),
                "max_voltage_pu": round(float(np.abs(sol.V).max()), 4),
                "base_case_overloads_pct": base_overloads,
                "n1_violations": {k: len(v) for k, v in result.contingency_violations.items()},
            },
            intermediates={
                "mismatch_trace": result.convergence_trace,
                "jacobian_size": (None if sol.last_jacobian is None
                                  else list(sol.last_jacobian.shape)),
                "dc_vs_ac_flow_sample_mw": {"dc": dc_flow_sample, "ac": ac_flow_sample,
                                            "note": "DC omits the losses AC reveals"},
            },
            provenance={"engine": "pf", "version": ENGINE_VERSION,
                        "input_facets": self.facets,
                        "handoff": "PCM dispatch -> power flow (Section 6.7)"},
        )
