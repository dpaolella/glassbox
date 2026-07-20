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
    # balancing (islanded toy: |B| is the whole interconnection's bias here)
    bias_mw_per_0p1hz: float = -80.0
    agc_gain: float = 0.35           # fraction of ACE corrected per subtick
    agc_subticks: int = 12           # emulated AGC cycles per 5-min step
    # markets
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
            "max_rho", "lambda_per_mwh")}
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
        view.lines = [ln for ln in view.lines
                      if ln.id not in self._tripped_lines]
        for ln in view.lines:
            if ln.id in self._line_derate:
                ln.rating *= self._line_derate[ln.id]
        return view

    # --- stage 0: day-ahead ------------------------------------------------

    def run_day_ahead(self) -> dict:
        from ..engines.economic_core import (EngineOptions,
                                             build_dispatch_model, solve_model)

        n_hours = math.ceil(self.cfg.n_steps * self.cfg.step_minutes / 60)
        h0 = self.cfg.weather_year * HOURS_PER_YEAR + self.cfg.start_hour
        hours = np.arange(h0, h0 + n_hours)
        view = self._view(self.world, hours, actual=False, step0=0)
        view.T = len(hours)
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
        view = self._view(derived, hours, actual=True, step0=step)
        fixed = {}
        for gid, arr in self._commitment.items():
            idx = ((hours - hours[0]) * 0 +
                   (self._abs_hours(step, T) - self._abs_hours(0, 1)[0])
                   * 60 // 60)  # hour offsets from shift start
            offs = np.clip(((np.arange(T) * self.cfg.step_minutes
                             + step * self.cfg.step_minutes) // 60),
                           0, len(arr) - 1).astype(int)
            fixed[gid] = arr[offs]
        built = build_dispatch_model(view, EngineOptions(
            investment=False, unit_commitment=bool(fixed), reserves=True,
            label="rt_sced", fixed_commitment=fixed or None))
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
        if "sto_discharge" in built.m.variables:
            dis = built.m.variables["sto_discharge"].solution
            ch = built.m.variables["sto_charge"].solution
            self._storage_net = (dis.sum("s") - ch.sum("s")).values
        if "unserved" in built.m.variables:
            self._sced_unserved = \
                built.m.variables["unserved"].solution.sum("n").values
        self._window_start = step
        self._window_load = view.load          # [n_nodes, T] actuals
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
        self._da = self.run_day_ahead()
        self._freq_nominal = self.world.base_frequency_hz
        self._b_total = abs(cfg.bias_mw_per_0p1hz) * 10.0  # MW per Hz
        self._unserved_mwh = 0.0
        self._energy_cost = 0.0
        self._redispatch: dict[str, float] = {}   # operator basepoint offsets
        self._shed_mw = 0.0                        # operator manual load shed
        self.k = 0

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
            resched = {"generator_trip", "line_trip", "line_reclosed"}
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
            unserved = sched_short + max(0.0, -ace)
            # islanded Reporting ACE: interchange term is zero
            freq = freq_nominal + ace / b_total
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

    # --- helpers -------------------------------------------------------------

    def _actual_load_mw(self, k: int, derived: World) -> float:
        w = min(k - self._window_start, self._window_load.shape[1] - 1)
        return float(self._window_load[:, w].sum())

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
        w = min(len(self.traces["load_mw"]) - self._window_start,
                self._window_load.shape[1] - 1)
        for i, node in enumerate(self._window_nodes):
            if node in idx:
                inj[idx[node]] -= float(self._window_load[i, max(w, 0)])
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
        ev = {"step": k, "kind": "generator_trip", "id": gid, "reason": reason}
        # SFR transient: how deep does frequency dip before governors arrest it?
        try:
            from ..engines.dynamics import (assemble_frequency_system,
                                            simulate_frequency_response)
            bp = {g: float(v[0]) for g, v in self._basepoints.items()} \
                if self._basepoints else {}
            lost = bp.get(gid) or next(
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
