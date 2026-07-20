"""The rtops kernel: one operating shift, headless (issue #56 Phase 0b).

Staged architecture per the PRD (§3), borrowed from Sienna PowerSimulations:

  Stage 0  Day-ahead: hourly unit commitment over the operating day on the
           FORECAST (the time-series store is the forecast; actuals deviate
           from it). Produces the commitment schedule — the feedforward.
  Stage 1  Real-time SCED: a 5-minute-resolution dispatch LP re-solved each
           hour of the shift with the DA commitment FIXED
           (``EngineOptions.fixed_commitment`` — the engine's existing
           Sienna-style SemiContinuousFeedforward), on ACTUALS, over the
           topology-processed network. Basepoints + system lambda out.
  Stage 2  AGC emulation: within each 5-min step, regulation-eligible
           committed units chase the ACE toward zero within their headroom;
           frequency deviation follows the areal bias. (Toy system is an
           island: the interchange term of Reporting ACE is zero, so
           ACE = -10B(Fa - Fs) reduces to the imbalance itself. The external
           area + tie schedule arrive with the OperatingArea entity, Phase 2.)
  Stage 3  Network & protection: DC flows of the realized injections on the
           derived bus-branch network; rho against the three-tier ratings;
           Grid2Op-style soft/hard overflow rules trip lines THROUGH their
           bay breakers (where the arrangement has them), with a forced
           reconnection timer. Trip -> re-solve -> next trip is the cascade.

Determinism: everything random flows from one seeded generator; the same
``ShiftConfig`` yields a byte-identical event log and traces (CI-tested).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..schema import World
from .elaborate import elaborate_world
from .switching import operate_switch
from .topology import derive_bus_branch

HOURS_PER_YEAR = 8760


@dataclass
class ShiftConfig:
    """A seeded operating day. (Becomes the ShiftScenario entity in Phase 2.)"""

    seed: int = 42
    start_hour: int = 5              # 05:00, the turnover briefing hour
    n_steps: int = 144               # 12 h at 5-minute steps
    step_minutes: int = 5
    weather_year: int = 0
    # actuals = forecast x bounded random walk (the gridfm-datakit recipe)
    load_error_sigma: float = 0.01   # per-step walk scale
    vre_error_sigma: float = 0.02
    # balancing / interconnection context (overridden by a world
    # OperatingArea when one exists)
    bias_mw_per_0p1hz: float = -80.0          # the area's own B (negative)
    external_bias_mw_per_0p1hz: float = -1500.0   # rest-of-interconnection
    tie_capacity_mw: float = 400.0            # total tie transfer capability
    interchange_schedule_mw: list[float] = field(default_factory=list)
    # hourly NIs, exports positive; empty = zero schedule (islanded balance)
    agc_gain: float = 0.35           # fraction of ACE corrected per subtick
    agc_subticks: int = 12           # emulated AGC cycles per 5-min step
    # markets
    reserve_curve: list[tuple[float, float]] = field(
        default_factory=lambda: [(0.5, 300.0), (1.0, 2000.0)])
    # ORDC-lite: shortage price rises as reserves deepen; couples into LMP
    sced_every_steps: int = 12       # re-solve the RT window hourly
    sced_window_steps: int = 12      # 1-hour lookahead at 5-min resolution
    # protection (Grid2Op parameter analogs, PRD §3.4)
    overflow_steps_allowed: int = 2  # NB_TIMESTEP_OVERFLOW_ALLOWED
    hard_overflow_multiple: float = 1.5   # x emergency rating -> instant trip
    reconnect_steps: int = 6         # NB_TIMESTEP_RECONNECTION (30 min)
    forced_outages: bool = True
    scripted_events: list[dict] = field(default_factory=list)
    # e.g. {"step": 30, "kind": "trip_generator", "id": "coal_1"}
    #      {"step": 10, "kind": "derate_line", "id": "l4", "factor": 0.3}


@dataclass
class ShiftResult:
    config: ShiftConfig
    events: list[dict]
    traces: dict[str, list]          # per-step: freq_hz, ace_mw, load_mw, ...
    totals: dict[str, float]
    da_summary: dict

    def to_json(self) -> dict:
        return {"config": self.config.__dict__ | {
                    "scripted_events": self.config.scripted_events},
                "events": self.events, "traces": self.traces,
                "totals": self.totals, "da_summary": self.da_summary}


class OpsSimulation:
    """One shift on one world. Mutates only switch state (restored on close)."""

    def __init__(self, world: World, cfg: ShiftConfig):
        self.world = elaborate_world(world)
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.events: list[dict] = []
        self.traces: dict[str, list] = {k: [] for k in (
            "freq_hz", "ace_mw", "load_mw", "gen_mw", "unserved_mw",
            "max_rho", "lambda_per_mwh", "ni_sched_mw", "ni_actual_mw")}
        # dynamic state
        self._tripped_lines: dict[str, int] = {}      # id -> steps to reconnect
        self._out_gens: dict[str, int] = {}           # id -> steps to repair
        self._overflow_count: dict[str, int] = {}
        self._line_derate: dict[str, float] = {}
        self._basepoints: dict[str, np.ndarray] = {}  # gen -> MW over window
        self._window_start = 0
        self._agc_adjust: dict[str, float] = {}
        self._prev_setpoint: dict[str, float] = {}
        self._switch_ops: list[tuple[str, bool]] = []  # to undo at the end

    # --- shared assembly ---------------------------------------------------

    def _abs_hours(self, start_step: int, n_steps: int) -> np.ndarray:
        h0 = self.cfg.weather_year * HOURS_PER_YEAR + self.cfg.start_hour
        mins = start_step * self.cfg.step_minutes
        return np.array([h0 + (mins + i * self.cfg.step_minutes) // 60
                         for i in range(n_steps)])

    def _view(self, world: World, hours: np.ndarray, actual: bool,
              step0: int):
        """Assemble an EconomicView; optionally overwrite forecast w/ actuals."""
        from ..engines import assemble_view
        from ..operators.spatial import SpatialMode, SpatialProjection

        sview = SpatialProjection(SpatialMode.IDENTITY).apply(world)
        T = len(hours)
        weight = np.full(T, self.cfg.step_minutes / 60.0)
        view = assemble_view(world, sview, hours % HOURS_PER_YEAR,
                             np.full(T, self.cfg.weather_year, dtype=int),
                             weight, 1.0, investment=False)
        if actual:
            for t in range(T):
                view.load[:, t] *= self._load_factor[step0 + t]
            for g in view.gens:
                if g.is_vre and g.availability is not None:
                    for t in range(T):
                        g.availability[t] = float(np.clip(
                            g.availability[t]
                            * self._vre_factor.get(g.id, self._ones)[step0 + t],
                            0.0, 1.0))
        # kernel-level outages and protection trips
        out = {g for g, left in self._out_gens.items() if left > 0}
        for g in view.gens:
            if g.id in out:
                g.availability = np.zeros(T) if g.availability is None \
                    else g.availability * 0.0
                g.p_min_pu = 0.0   # an outaged unit has no must-run minimum
        view.lines = [ln for ln in view.lines
                      if ln.id not in self._tripped_lines]
        for ln in view.lines:
            if ln.id in self._line_derate:
                ln.rating *= self._line_derate[ln.id]
        return view

    def _ref_idx(self, view) -> int:
        ref = self.world.reference_bus_id
        return view.nodes.index(ref) if ref in view.nodes else 0

    # --- stage 0: day-ahead ------------------------------------------------

    def run_day_ahead(self) -> dict:
        from ..engines.economic_core import (EngineOptions,
                                             build_dispatch_model, solve_model)

        # commit over at least a half-day regardless of shift length: a
        # too-short UC horizon cannot start long-min-up units (boundary
        # artifact) and sheds load a full-day commitment would serve
        n_hours = max(math.ceil(self.cfg.n_steps * self.cfg.step_minutes / 60),
                      12)
        h0 = self.cfg.weather_year * HOURS_PER_YEAR + self.cfg.start_hour
        hours = np.arange(h0, h0 + n_hours)
        view = self._view(self.world, hours, actual=False, step0=0)
        view.T = len(hours)
        if np.any(self._ni):
            ridx = self._ref_idx(view)
            sph = 60 // self.cfg.step_minutes
            for t in range(len(hours)):
                view.load[ridx, t] += self._ni[min(t * sph + sph // 2,
                                                   len(self._ni) - 1)]
        built = build_dispatch_model(view, EngineOptions(
            investment=False, unit_commitment=True, reserves=True, label="da_uc"))
        status = solve_model(built)
        if "ok" not in status and "optimal" not in status.lower():
            raise RuntimeError(f"day-ahead UC failed: {status}")
        self._commitment: dict[str, np.ndarray] = {}
        uc_ids = built.meta.get("uc_ids", [])
        if uc_ids and "commit" in built.m.variables:
            sol = built.m.variables["commit"].solution
            for gid in uc_ids:
                self._commitment[gid] = np.round(
                    sol.sel(g=gid).values).astype(float)
        cost = float(built.m.objective.value)
        peak = float(view.load.sum(axis=0).max())
        self.events.append({"step": -1, "kind": "turnover_briefing",
                            "detail": f"DA-UC committed {len(uc_ids)} UC units; "
                                      f"forecast peak {peak:.0f} MW",
                            "da_cost": cost})
        return {"committed_units": {g: arr.tolist()
                                    for g, arr in self._commitment.items()},
                "forecast_peak_mw": peak, "da_cost": cost,
                "status": status}

    # --- stage 1: RT-SCED ---------------------------------------------------

    def _run_sced(self, step: int, derived: World) -> None:
        from ..engines.economic_core import (EngineOptions,
                                             build_dispatch_model, solve_model)

        T = min(self.cfg.sced_window_steps, self.cfg.n_steps - step)
        hours = self._abs_hours(step, T)
        view = self._view(derived, hours, actual=False, step0=step)
        self._window_native = view.load.copy()      # store forecast, no NIs
        f_now = float(self._load_factor[step])       # persistence forecast
        ridx = self._ref_idx(view)
        self._window_ref_idx = ridx
        for t in range(T):
            view.load[:, t] *= f_now
            view.load[ridx, t] += self._ni[step + t]
        for g in view.gens:
            if g.is_vre and g.availability is not None:
                vf = float(self._vre_factor.get(g.id, self._ones)[step])
                g.availability = np.clip(g.availability * vf, 0.0, 1.0)
        fixed = {}
        for gid, arr in self._commitment.items():
            idx = ((hours - hours[0]) * 0 +
                   (self._abs_hours(step, T) - self._abs_hours(0, 1)[0])
                   * 60 // 60)  # hour offsets from shift start
            offs = np.clip(((np.arange(T) * self.cfg.step_minutes
                             + step * self.cfg.step_minutes) // 60),
                           0, len(arr) - 1).astype(int)
            fixed[gid] = arr[offs]
        # a unit on forced outage is decommitted in the RT case — otherwise
        # u=1 demands p >= p_min while zero availability demands p <= 0 and
        # the LP is infeasible (the EMS outage scheduler does this for real)
        for gid in self._out_gens:
            if gid in fixed:
                fixed[gid] = np.zeros(T)
        built = build_dispatch_model(view, EngineOptions(
            investment=False, unit_commitment=bool(fixed), reserves=True,
            label="rt_sced", fixed_commitment=fixed or None,
            storage_soc_init=dict(self._soc) if self._soc else None,
            reserve_curve=self.cfg.reserve_curve or None))
        status = solve_model(built)
        if "ok" not in status and "optimal" not in status.lower():
            self.events.append({"step": step, "kind": "sced_failed",
                                "detail": status})
            return
        sol = built.m.variables["gen_p"].solution
        self._basepoints = {str(g): sol.sel(g=g).values
                            for g in sol.coords["g"].values}
        self._storage_net = None
        self._sced_unserved = None
        self._sto_flows = {}
        if "sto_discharge" in built.m.variables:
            dis = built.m.variables["sto_discharge"].solution
            ch = built.m.variables["sto_charge"].solution
            self._storage_net = (dis.sum("s") - ch.sum("s")).values
            eff = {st.id: (st.eff_c, st.eff_d) for st in view.storages}
            for sid in dis.coords["s"].values:
                self._sto_flows[str(sid)] = (ch.sel(s=sid).values,
                                             dis.sel(s=sid).values,
                                             eff.get(str(sid), (0.9, 0.9)))
        if "unserved" in built.m.variables:
            self._sced_unserved = \
                built.m.variables["unserved"].solution.sum("n").values
        self._window_start = step
        self._window_nodes = list(view.nodes)
        # system lambda: the cost of the marginal dispatched unit
        mc = {g.id: g.marginal_cost for g in view.gens}
        lam = 0.0
        for gid, bp in self._basepoints.items():
            cap = next((g.p_nom_existing for g in view.gens if g.id == gid), 0)
            if bp[0] > 1e-3 and bp[0] < cap - 1e-3:
                lam = max(lam, mc.get(gid, 0.0))
        self._lambda = lam or max((mc[g] for g, bp in self._basepoints.items()
                                   if bp[0] > 1e-3), default=0.0)
        # ORDC-lite scarcity adder: when a reserve tranche is short, its
        # price is the marginal value of capacity — prices scream BEFORE
        # load is shed (the energy-only market's central design idea)
        if self.cfg.reserve_curve:
            for i, (_frac, price) in enumerate(self.cfg.reserve_curve):
                vname = f"reserve_short_t{i}"
                if vname in built.m.variables:
                    short = float(built.m.variables[vname]
                                  .solution.isel(t=0).item())
                    if short > 1e-3:
                        self._lambda = max(self._lambda, price)

    # --- the step loop -------------------------------------------------------

    def start(self) -> None:
        """Precompute actuals walks + run day-ahead; ready to advance."""
        cfg = self.cfg
        n = cfg.n_steps + cfg.sced_window_steps
        walk = np.cumsum(self.rng.normal(0, cfg.load_error_sigma, n))
        self._load_factor = np.clip(1.0 + walk, 0.9, 1.1)
        self._ones = np.ones(n)
        self._vre_factor = {}
        for g in self.world.generators:
            if g.technology.value in ("wind", "solar_pv"):
                w = np.cumsum(self.rng.normal(0, cfg.vre_error_sigma, n))
                self._vre_factor[g.id] = np.clip(1.0 + w, 0.5, 1.3)
        self._hazard = {g.id: (cfg.step_minutes / 60.0) / g.mttf_h
                        for g in self.world.generators
                        if cfg.forced_outages and (g.mttf_h or 0) > 0}
        self._outage_draws = {gid: self.rng.random(cfg.n_steps)
                              for gid in sorted(self._hazard)}
        if self.world.operating_areas:
            oa = self.world.operating_areas[0]
            self.cfg.bias_mw_per_0p1hz = oa.frequency_bias_mw_per_0p1hz
            self.cfg.external_bias_mw_per_0p1hz = oa.external_bias_mw_per_0p1hz
            self.cfg.tie_capacity_mw = oa.tie_capacity_mw
            if oa.scheduled_interchange_mw:
                self.cfg.interchange_schedule_mw = \
                    list(oa.scheduled_interchange_mw)
        self._tie_available = self.cfg.tie_capacity_mw > 0
        self._ni = self._build_ni_schedule()
        self._soc = {st.id: 0.5 for st in self.world.storage_units
                     if st.in_service}
        self._da = self.run_day_ahead()
        self._freq_nominal = self.world.base_frequency_hz
        self._b_total = abs(cfg.bias_mw_per_0p1hz) * 10.0  # MW per Hz
        self._unserved_mwh = 0.0
        self._energy_cost = 0.0
        self._redispatch: dict[str, float] = {}   # operator basepoint offsets
        self._shed_mw = 0.0                        # operator manual load shed
        self._eea_level = 0
        self._sol_clocks: dict[str, int] = {}
        self.k = 0

    def _build_ni_schedule(self) -> np.ndarray:
        """Per-step scheduled net interchange NIs (exports +), with the
        real-world :50 -> :10 ramp across hourly schedule changes."""
        cfg = self.cfg
        n = cfg.n_steps + cfg.sced_window_steps
        hourly = cfg.interchange_schedule_mw or []
        if not hourly:
            return np.zeros(n)
        def h_val(h):
            return float(hourly[min(max(h, 0), len(hourly) - 1)])
        out = np.zeros(n)
        start_min = cfg.start_hour * 60
        for k in range(n):
            m = start_min + k * cfg.step_minutes
            h, m_in = divmod(m, 60)
            h -= cfg.start_hour
            if m_in >= 50:      # ramping toward next hour's schedule
                frac = (m_in - 50 + cfg.step_minutes) / 20.0
                out[k] = (1 - frac) * h_val(h) + frac * h_val(h + 1)
            elif m_in < 10:     # finishing the ramp from the previous hour
                frac = (m_in + 10 + cfg.step_minutes) / 20.0
                out[k] = (1 - frac) * h_val(h - 1) + frac * h_val(h)
            else:
                out[k] = h_val(h)
        return out

    @property
    def finished(self) -> bool:
        return self.k >= self.cfg.n_steps

    def run(self) -> ShiftResult:
        self.start()
        while not self.finished:
            self.advance_one()
        return self.finish()

    def advance_one(self) -> None:
        cfg = self.cfg
        n = cfg.n_steps + cfg.sced_window_steps
        hazard, outage_draws = self._hazard, self._outage_draws
        freq_nominal, b_total = self._freq_nominal, self._b_total
        for k in [self.k]:
            # 1) scripted + stochastic events
            for ev in cfg.scripted_events:
                if ev.get("step") == k:
                    self._apply_event(k, ev)
            for gid in sorted(hazard):
                if gid not in self._out_gens and \
                        outage_draws[gid][k] < hazard[gid]:
                    mttr = next((g.mttr_h or 24.0)
                                for g in self.world.generators
                                if g.id == gid)
                    self._trip_generator(k, gid,
                                         int(mttr * 60 / cfg.step_minutes),
                                         reason="forced outage")
            # decrement repair/reconnect timers
            self._out_gens = {g: v - 1 for g, v in self._out_gens.items()
                              if v - 1 > 0}
            for lid in [l for l, v in self._tripped_lines.items() if v - 1 <= 0]:
                self._reclose_line(k, lid)
            self._tripped_lines = {l: v - 1
                                   for l, v in self._tripped_lines.items()
                                   if v - 1 > 0}

            # 2) topology processing
            topo = derive_bus_branch(self.world)
            derived = topo.world

            # 3) RT-SCED on schedule, or promptly after a contingency.
            # Rating changes (derates) are RTCA inputs, not market events —
            # SCED sees them at its next scheduled solve, which is exactly
            # why overloads happen BETWEEN solves in the real world too.
            resched = {"generator_trip", "line_trip", "line_reclosed", "tie_trip"}
            if k % cfg.sced_every_steps == 0 or k == 0 or \
                    any(e["step"] in (k, k - 1) and e["kind"] in resched
                        for e in self.events):
                self._run_sced(k, derived)

            w = k - self._window_start
            bp = {g: float(v[min(w, len(v) - 1)])
                  for g, v in self._basepoints.items()}
            for gid, dmw in self._redispatch.items():
                if gid in bp:
                    bp[gid] = max(0.0, bp[gid] + dmw)

            # 4) AGC emulation toward ACE = 0
            hour_idx = min((k * cfg.step_minutes) // 60 * 0 + k, n - 1)
            actual_load = max(0.0, self._actual_load_mw(k, derived)
                              - self._shed_mw)
            sto = 0.0
            if getattr(self, "_storage_net", None) is not None:
                sto = float(self._storage_net[min(w, len(self._storage_net) - 1)])
            gen_total = sum(bp.values()) + sto + sum(self._agc_adjust.values())
            # load the market already scheduled as unserved is a firm
            # shortfall, not a regulation error: AGC must not chase it
            sched_short = 0.0
            if getattr(self, "_sced_unserved", None) is not None:
                sched_short = float(
                    self._sced_unserved[min(w, len(self._sced_unserved) - 1)])
            reg_pool = self._regulation_headroom(k, derived, bp)
            freq = freq_nominal
            ace = gen_total + sched_short - actual_load
            for _ in range(cfg.agc_subticks):
                correction = -cfg.agc_gain * ace
                correction = float(np.clip(correction, -reg_pool, reg_pool))
                gen_total += correction
                self._distribute_agc(correction, derived, bp)
                ace = gen_total + sched_short - actual_load
            # physical shortfall (what customers experience); sched_short
            # stays inside ACE so AGC treats market-scheduled shortage as
            # firm, not as regulation error
            unserved = max(0.0, actual_load - gen_total)
            # Reporting ACE = (NIa - NIs) - 10B(Fa - Fs). The external area
            # (big bias behind the ties) absorbs its share of the imbalance
            # as inadvertent interchange until the tie saturates; whatever
            # the tie cannot carry moves frequency against the area's own
            # bias alone. Algebra: ACE == the area's own imbalance, always —
            # the definition's whole point.
            ni_s = float(self._ni[k])
            cap = cfg.tie_capacity_mw if self._tie_available else 0.0
            bext = 10.0 * abs(cfg.external_bias_mw_per_0p1hz) \
                if self._tie_available else 0.0
            support = ace * bext / (b_total + bext) if bext > 0 else 0.0
            support = float(np.clip(support, -(cap + ni_s), cap - ni_s))
            ni_actual = ni_s + support
            freq = freq_nominal + (ace - support) / b_total
            self._unserved_mwh += unserved * cfg.step_minutes / 60.0
            self._energy_cost += self._step_cost(bp) * cfg.step_minutes / 60.0

            # 5) protection on realized flows
            max_rho = self._protection_pass(k, derived, bp, actual_load)

            self.traces["freq_hz"].append(round(freq, 5))
            self.traces["ace_mw"].append(round(ace, 3))
            self.traces["load_mw"].append(round(actual_load, 3))
            self.traces["gen_mw"].append(round(gen_total, 3))
            self.traces["unserved_mw"].append(round(unserved, 3))
            self.traces["max_rho"].append(round(max_rho, 4))
            self.traces["lambda_per_mwh"].append(round(getattr(self, "_lambda", 0.0), 2))
            self.traces["ni_sched_mw"].append(round(ni_s, 2))
            self.traces["ni_actual_mw"].append(round(ni_actual, 2))
            dt_h = cfg.step_minutes / 60.0
            for st_ in self.world.storage_units:
                if st_.id not in getattr(self, "_sto_flows", {}):
                    continue
                chv, disv, (ec, ed) = self._sto_flows[st_.id]
                wi = min(w, len(chv) - 1)
                e_nom = max(st_.energy_capacity_mwh, 1e-6)
                d_soc = (ec * float(chv[wi]) - float(disv[wi]) / ed) * dt_h / e_nom
                self._soc[st_.id] = float(np.clip(
                    self._soc.get(st_.id, 0.5) + d_soc,
                    st_.soc_min_pu, st_.soc_max_pu))
            self._assess_reliability(k, derived, bp, unserved)
        self.k += 1

    def finish(self) -> ShiftResult:
        # restore all switch state the run changed
        for sw_id, prev in reversed(self._switch_ops):
            sw = next(s for s in self.world.switches if s.id == sw_id)
            sw.open = prev
        self._switch_ops.clear()
        return ShiftResult(
            config=self.cfg, events=self.events, traces=self.traces,
            totals=self.totals(), da_summary=self._da)

    def totals(self) -> dict:
        trips = sum(1 for e in self.events if e["kind"] == "line_trip")
        return {"unserved_mwh": round(self._unserved_mwh, 4),
                "energy_cost": round(self._energy_cost, 2),
                "line_trips": trips,
                "gen_outages": sum(1 for e in self.events
                                   if e["kind"] == "generator_trip"),
                "max_freq_dev_hz": round(max(
                    (abs(f - self._freq_nominal)
                     for f in self.traces["freq_hz"]), default=0.0), 5)}

    # --- reliability assessment: EEA ladder + RTCA N-1 (Phase 2) -----------

    def _assess_reliability(self, k, derived, bp, unserved) -> None:
        """The simulated RC watching over your desk (PRD §5): declares EEA
        levels from reserve posture and runs a one-deep RTCA screen with the
        30-minute SOL clock (TOP-001's Real-time Assessment cadence)."""
        # EEA: contingency reserve requirement = largest online unit (BAL-002)
        online = [g for g in derived.generators
                  if g.in_service and g.id not in self._out_gens
                  and bp.get(g.id, 0.0) > 1e-3]
        largest = max((bp[g.id] for g in online), default=0.0)
        headroom = sum(max(0.0, g.p_max_mw - bp.get(g.id, 0.0)) for g in online)
        if unserved > 1.0:
            level = 3
        elif headroom < 0.5 * largest:
            level = 2
        elif headroom < largest:
            level = 1
        else:
            level = 0
        if level != self._eea_level:
            msg = {0: "EEA cancelled — reserves restored",
                   1: "EEA-1: all resources committed, reserves below "
                      "requirement (largest online unit)",
                   2: "EEA-2: reserves critically deficient — deploy demand "
                      "response, prepare load management",
                   3: "EEA-3: firm load interruption imminent or in progress"}
            self.events.append({"step": k, "kind": "rc_directive",
                                "eea_level": level, "detail": msg[level]})
            self._eea_level = level

        # RTCA lite: outage the heaviest-loaded line, re-solve, flag post-
        # contingency overloads; each carries a 30-minute SOL clock.
        flows = getattr(self, "last_flows", {})
        live = [l for l in derived.ac_lines
                if l.in_service and l.id not in self._tripped_lines]
        if len(live) > 2 and flows:
            heaviest = max((l for l in live if l.id in flows),
                           key=lambda l: abs(flows[l.id]), default=None)
            if heaviest is not None:
                self._tripped_lines[heaviest.id] = 1     # pretend, briefly
                post = self._dc_flows(derived, bp)
                del self._tripped_lines[heaviest.id]
                viol = []
                for l in live:
                    if l.id == heaviest.id or l.id not in post:
                        continue
                    rho = abs(post[l.id]) / max(
                        l.rating_emergency_mva
                        * self._line_derate.get(l.id, 1.0), 1e-6)
                    if rho > 1.0:
                        viol.append((l.id, rho))
                sol_steps = int(30 / self.cfg.step_minutes)
                for lid, rho in viol:
                    self._sol_clocks[lid] = self._sol_clocks.get(lid, 0) + 1
                    if self._sol_clocks[lid] == 1:
                        self.events.append({
                            "step": k, "kind": "rtca_violation", "id": lid,
                            "detail": f"if {heaviest.id} trips, {lid} reaches "
                                      f"{rho:.2f}x emergency — SOL clock "
                                      "started (30 min to mitigate)"})
                    elif self._sol_clocks[lid] == sol_steps + 1:
                        self.events.append({
                            "step": k, "kind": "sol_clock_expired", "id": lid,
                            "detail": f"post-contingency overload on {lid} "
                                      "uncleared for 30 minutes — TOP-001 "
                                      "compliance violation"})
                cleared = [lid for lid in self._sol_clocks
                           if lid not in {v[0] for v in viol}]
                for lid in cleared:
                    if self._sol_clocks[lid] > 0:
                        self.events.append({"step": k, "kind": "sol_cleared",
                                            "id": lid})
                    del self._sol_clocks[lid]

    # --- helpers -------------------------------------------------------------

    def _actual_load_mw(self, k: int, derived: World) -> float:
        w = min(k - self._window_start, self._window_native.shape[1] - 1)
        return float(self._window_native[:, w].sum()
                     * self._load_factor[k] + self._ni[k])

    def _regulation_headroom(self, k, derived, bp) -> float:
        out = set(self._out_gens)
        room = 0.0
        for g in derived.generators:
            if g.reserve_eligible and g.in_service and g.id not in out:
                room += max(0.0, g.p_max_mw - bp.get(g.id, 0.0))
        return room

    def _distribute_agc(self, correction, derived, bp) -> None:
        eligible = [g.id for g in derived.generators
                    if g.reserve_eligible and g.in_service
                    and g.id not in self._out_gens]
        if eligible:
            share = correction / len(eligible)
            for gid in eligible:
                self._agc_adjust[gid] = self._agc_adjust.get(gid, 0.0) + share

    def _step_cost(self, bp) -> float:
        mc = getattr(self, "_mc_cache", None)
        if mc is None:
            from ..engines.economic_core import _gen_marginal_cost
            mc = {g.id: _gen_marginal_cost(self.world, g)[0]
                  for g in self.world.generators}
            self._mc_cache = mc
        return sum(p * mc.get(g, 0.0) for g, p in bp.items())

    def _protection_pass(self, k, derived, bp, load_mw) -> float:
        flows = self._dc_flows(derived, bp)
        self.last_flows = dict(flows)
        max_rho = 0.0
        cfg = self.cfg
        for line in derived.ac_lines:
            if not line.in_service or line.id in self._tripped_lines:
                continue
            f = abs(flows.get(line.id, 0.0))
            emergency = line.rating_emergency_mva * \
                self._line_derate.get(line.id, 1.0)
            normal = line.rating_normal_mva * \
                self._line_derate.get(line.id, 1.0)
            rho = f / max(emergency, 1e-6)
            max_rho = max(max_rho, f / max(normal, 1e-6))
            if rho >= cfg.hard_overflow_multiple:
                self._trip_line(k, line.id, "hard overflow "
                                f"({rho:.2f}x emergency rating)")
            elif rho > 1.0:
                c = self._overflow_count.get(line.id, 0) + 1
                self._overflow_count[line.id] = c
                if c > cfg.overflow_steps_allowed:
                    self._trip_line(k, line.id,
                                    f"soft overflow ({c} steps above "
                                    "emergency rating)")
                else:
                    self.events.append({"step": k, "kind": "overload_warning",
                                        "id": line.id,
                                        "detail": f"{rho:.2f}x emergency, "
                                                  f"step {c} of "
                                                  f"{cfg.overflow_steps_allowed}"})
            else:
                self._overflow_count.pop(line.id, None)
        return max_rho

    def _dc_flows(self, derived: World, bp) -> dict[str, float]:
        buses = [b.id for b in derived.buses]
        idx = {b: i for i, b in enumerate(buses)}
        nb = len(buses)
        inj = np.zeros(nb)
        for g in derived.generators:
            if g.in_service and g.bus_id in idx:
                p = bp.get(g.id, 0.0) + self._agc_adjust.get(g.id, 0.0)
                inj[idx[g.bus_id]] += p
        k_now = len(self.traces["load_mw"])
        w = max(min(k_now - self._window_start,
                    self._window_native.shape[1] - 1), 0)
        f_true = float(self._load_factor[min(k_now, len(self._load_factor) - 1)])
        for i, node in enumerate(self._window_nodes):
            if node in idx:
                load_i = float(self._window_native[i, w]) * f_true
                if i == self._window_ref_idx:
                    load_i += float(self._ni[min(k_now, len(self._ni) - 1)])
                inj[idx[node]] -= load_i
        inj -= inj.sum() / nb  # balance residual (AGC slop) uniformly
        B = np.zeros((nb, nb))
        lines = [ln for ln in derived.ac_lines
                 if ln.in_service and ln.id not in self._tripped_lines
                 and ln.from_bus_id in idx and ln.to_bus_id in idx]
        for ln in lines:
            b = 1.0 / max(ln.x, 1e-6)
            i, j = idx[ln.from_bus_id], idx[ln.to_bus_id]
            B[i, i] += b; B[j, j] += b; B[i, j] -= b; B[j, i] -= b
        theta = np.zeros(nb)
        keep = [i for i in range(nb) if i != 0]
        if keep and lines:
            try:
                theta[keep] = np.linalg.solve(B[np.ix_(keep, keep)], inj[keep])
            except np.linalg.LinAlgError:
                return {}
        base = self.world.base_power_mva
        return {ln.id: (theta[idx[ln.from_bus_id]]
                        - theta[idx[ln.to_bus_id]]) / max(ln.x, 1e-6)
                for ln in lines}

    # --- events ----------------------------------------------------------------

    def _apply_event(self, k: int, ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "trip_generator":
            self._trip_generator(k, ev["id"],
                                 ev.get("repair_steps", 10**9),
                                 reason="scripted trip")
        elif kind == "derate_line":
            self._line_derate[ev["id"]] = float(ev.get("factor", 0.5))
            self.events.append({"step": k, "kind": "line_derated",
                                "id": ev["id"], "factor": ev.get("factor", 0.5)})
        elif kind == "trip_tie":
            self._tie_available = False
            self._ni[k:] = 0.0     # emergency schedule curtailment
            self.events.append({"step": k, "kind": "tie_trip",
                                "detail": "tie to the interconnection lost — "
                                          "islanded: schedules curtailed, "
                                          "frequency now moves against the "
                                          "area's own bias alone"})
        elif kind == "scale_load":
            f = float(ev.get("factor", 1.1))
            self._load_factor[k:] = np.clip(self._load_factor[k:] * f, 0.5, 2.0)
            self.events.append({"step": k, "kind": "load_scaled", "factor": f})

    def _trip_generator(self, k, gid, repair_steps, reason) -> None:
        self._out_gens[gid] = max(repair_steps, 1)
        # protection acts through the bay breaker where one exists
        cb = f"cb__{gid}__1"
        if any(s.id == cb for s in self.world.switches):
            sw = next(s for s in self.world.switches if s.id == cb)
            self._switch_ops.append((cb, sw.open))
            operate_switch(self.world, cb, True)
        lost_now = 0.0
        if self._basepoints and gid in self._basepoints:
            lost_now = float(self._basepoints[gid][0])
        ev = {"step": k, "kind": "generator_trip", "id": gid, "reason": reason,
              "lost_mw": round(lost_now, 1)}
        # SFR transient: how deep does frequency dip before governors arrest it?
        try:
            from ..engines.dynamics import (assemble_frequency_system,
                                            simulate_frequency_response)
            bp = {g: float(v[0]) for g, v in self._basepoints.items()} \
                if self._basepoints else {}
            lost = lost_now or next(
                (g.p_max_mw * 0.5 for g in self.world.generators
                 if g.id == gid), 0.0)
            sys = assemble_frequency_system(self.world, bp or {gid: lost})
            sfr = simulate_frequency_response(sys, event_mw=lost)
            ev["sfr"] = {"nadir_hz": sfr.nadir_hz,
                         "rocof_hz_per_s": sfr.rocof_hz_per_s}
        except Exception as exc:  # SFR is display-grade; never kills the shift
            ev["sfr_error"] = str(exc)
        self.events.append(ev)

    def _trip_line(self, k, lid, reason) -> None:
        if lid in self._tripped_lines:
            return
        self._tripped_lines[lid] = self.cfg.reconnect_steps
        self._overflow_count.pop(lid, None)
        for seq in (1, 2):
            cb = f"cb__{lid}__{seq}"
            if any(s.id == cb for s in self.world.switches):
                sw = next(s for s in self.world.switches if s.id == cb)
                self._switch_ops.append((cb, sw.open))
                operate_switch(self.world, cb, True)
        self.events.append({"step": k, "kind": "line_trip", "id": lid,
                            "reason": reason,
                            "reconnect_steps": self.cfg.reconnect_steps})

    def _reclose_line(self, k, lid) -> None:
        for seq in (1, 2):
            cb = f"cb__{lid}__{seq}"
            if any(s.id == cb for s in self.world.switches):
                operate_switch(self.world, cb, False)
        self.events.append({"step": k, "kind": "line_reclosed", "id": lid})


def run_shift(world: World, cfg: ShiftConfig | None = None) -> ShiftResult:
    return OpsSimulation(world, cfg or ShiftConfig()).run()
