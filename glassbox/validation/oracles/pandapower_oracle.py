"""pandapower oracle for the AC power flow engine (PRD Sections 11.1, 11.2).

The PRD uses mature libraries as *correctness oracles* alongside the transparent
kernel, not as the production path (Section 2.3). Here we rebuild the same
operating point (same dispatch snapshot, same line/transformer/shunt models, same
VAR compensation and PV/PQ classification as ``assemble_pf_case``) in pandapower,
run its AC power flow, and compare voltages, angles, and branch flows against the
hand-built Newton-Raphson solution. Divergences are fidelity notes, not only test
failures.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...engines.powerflow import PFCase, assemble_pf_case, branch_flows, solve_newton_raphson
from ...schema import World

try:  # pandapower is a dev/test-only dependency
    import pandapower as pp
    HAVE_PANDAPOWER = True
except Exception:  # pragma: no cover - environment without the oracle
    pp = None
    HAVE_PANDAPOWER = False


def build_pandapower_net(world: World, case: PFCase):
    """Build a pandapower network mirroring ``case`` exactly.

    Reconstructs from the PFCase admittance: each branch becomes a pandapower
    element with the same series/charging admittance, and every pure bus shunt
    (line charging is on the branches, so this captures the VAR-compensation
    capacitors ``assemble_pf_case`` adds) is added as a shunt. Net P/Q injections
    come straight from ``case.p_spec/q_spec`` so the two solvers see one problem.
    """
    if not HAVE_PANDAPOWER:
        raise RuntimeError("pandapower not available")
    base = case.base_mva
    net = pp.create_empty_network(sn_mva=base, f_hz=world.base_frequency_hz)

    ppbus = {}
    for k, bid in enumerate(case.bus_ids):
        kv = world.bus(bid).base_kv
        ppbus[k] = pp.create_bus(net, vn_kv=kv, name=bid)

    # slack
    pp.create_ext_grid(net, ppbus[case.slack], vm_pu=float(case.v_set[case.slack]),
                       va_degree=0.0)

    # branches: rebuild from the case admittance so the network matches exactly
    diag_from_branches = np.zeros(case.n, dtype=complex)
    for br in case.branches:
        z = 1.0 / br.y_series
        if br.kind == "transformer":
            # series impedance only (default-world taps are 1.0)
            pp.create_impedance(net, ppbus[br.i], ppbus[br.j],
                                rft_pu=float(z.real), xft_pu=float(z.imag), sn_mva=base)
            t = br.tap
            diag_from_branches[br.i] += br.y_series / (abs(t) ** 2)
            diag_from_branches[br.j] += br.y_series
        else:
            zbase = world.bus(case.bus_ids[br.i]).base_kv ** 2 / base
            r_ohm = z.real * zbase
            x_ohm = z.imag * zbase
            # total charging susceptance b (pu) -> capacitance -> nF
            w = 2 * np.pi * world.base_frequency_hz
            c_farad = br.b_shunt / (w * zbase) if br.b_shunt else 0.0
            pp.create_line_from_parameters(
                net, ppbus[br.i], ppbus[br.j], length_km=1.0,
                r_ohm_per_km=max(r_ohm, 1e-9), x_ohm_per_km=max(x_ohm, 1e-9),
                c_nf_per_km=c_farad * 1e9, max_i_ka=100.0)
            diag_from_branches[br.i] += br.y_series + 1j * br.b_shunt / 2
            diag_from_branches[br.j] += br.y_series + 1j * br.b_shunt / 2

    # pure bus shunts = Ybus diagonal minus branch contributions (VAR comp etc.)
    for k in range(case.n):
        y_sh = case.Ybus[k, k] - diag_from_branches[k]
        # Q injected by a susceptance at V=1 is b·V²; pandapower shunt q_mvar
        # positive = inductive (consumes Q), so a capacitor (b>0) is negative.
        q_mvar = -float(y_sh.imag) * base
        p_mw = float(y_sh.real) * base
        if abs(q_mvar) > 1e-6 or abs(p_mw) > 1e-6:
            pp.create_shunt(net, ppbus[k], q_mvar=q_mvar, p_mw=p_mw)

    # injections: split net p_spec/q_spec into a gen (PV) or sgen (PQ) + nothing
    pv = set(case.pv)
    for k in range(case.n):
        if k == case.slack:
            continue
        p_mw = float(case.p_spec[k]) * base
        q_mvar = float(case.q_spec[k]) * base
        if k in pv:
            # voltage-regulating bus: hold |V|, supply net P (Q is free)
            pp.create_gen(net, ppbus[k], p_mw=p_mw, vm_pu=float(case.v_set[k]))
        else:
            # PQ bus: fixed net P and Q (sgen sign convention: + = injection)
            pp.create_sgen(net, ppbus[k], p_mw=p_mw, q_mvar=q_mvar)
    return net, ppbus


@dataclass
class PFComparison:
    converged_both: bool
    max_v_diff_pu: float
    max_angle_diff_deg: float
    max_flow_diff_mw: float
    losses_glassbox_mw: float
    losses_pandapower_mw: float
    n_buses: int


def compare_power_flow(world: World, hour: int, weather_year: int = 0,
                       dispatch_mode: str = "nodal") -> PFComparison:
    """Solve the same case with both kernels and return the discrepancies."""
    case = assemble_pf_case(world, hour, weather_year, dispatch_mode=dispatch_mode)
    sol = solve_newton_raphson(case)
    net, ppbus = build_pandapower_net(world, case)
    pp.runpp(net, calculate_voltage_angles=True, init="flat")

    v_gb = np.abs(sol.V)
    ang_gb = np.angle(sol.V, deg=True)
    v_pp = np.array([net.res_bus.vm_pu[ppbus[k]] for k in range(case.n)])
    ang_pp = np.array([net.res_bus.va_degree[ppbus[k]] for k in range(case.n)])
    # align angles to the slack reference (pandapower ext_grid at 0)
    ang_gb = ang_gb - ang_gb[case.slack]
    ang_pp = ang_pp - ang_pp[case.slack]

    flows_gb = branch_flows(case, sol.V)
    gb_loss = sum(f["loss_mw"] for f in flows_gb.values())
    pp_loss = float(net.res_line.pl_mw.sum()) if len(net.res_line) else 0.0
    if len(net.res_impedance):
        pp_loss += float(net.res_impedance.pl_mw.sum())

    # branch flow comparison (line elements, by creation order)
    flow_diffs = []
    line_branches = [br for br in case.branches if br.kind != "transformer"]
    for li, br in enumerate(line_branches):
        if li < len(net.res_line):
            gb = flows_gb[br.id]["p_from_mw"]
            ppf = float(net.res_line.p_from_mw[li])
            flow_diffs.append(abs(gb - ppf))

    return PFComparison(
        converged_both=bool(sol.converged and net.converged),
        max_v_diff_pu=float(np.max(np.abs(v_gb - v_pp))),
        max_angle_diff_deg=float(np.max(np.abs(ang_gb - ang_pp))),
        max_flow_diff_mw=float(max(flow_diffs) if flow_diffs else 0.0),
        losses_glassbox_mw=float(gb_loss),
        losses_pandapower_mw=float(pp_loss),
        n_buses=case.n,
    )
