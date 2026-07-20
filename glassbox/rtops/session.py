"""Interactive shift session: the server-side control room (issue #56 Phase 1).

The server holds one live ``OpsSimulation`` and advances it lazily whenever
the client polls: elapsed wall time x clock speed => due sim steps (capped per
poll). Freeze is simply "stop accruing". No background threads, and the
session survives tab switches by construction (the PRD's answer to the #50
class of bug).

Operator actions follow Grid2Op's legality model: anything not applicable
right now is rejected WITH THE REASON — the explanation is the teaching
affordance, so it always reaches the UI.
"""

from __future__ import annotations

import time

import numpy as np
from typing import Optional

from ..schema import World
from .kernel import OpsSimulation, ShiftConfig
from .switching import operate_switch

_SEVERITY = {"line_trip": "critical", "generator_trip": "critical",
             "overload_warning": "warning", "sced_failed": "critical",
             "line_derated": "warning", "load_scaled": "info",
             "line_reclosed": "info", "turnover_briefing": "info",
             "rc_directive": "critical", "rtca_violation": "warning",
             "sol_clock_expired": "critical", "sol_cleared": "info",
             "breaker_failure": "critical", "breaker_stuck_armed": "info",
             "clearance_active": "info", "clearance_released": "info",
             "se_degraded": "critical", "bad_data_identified": "warning",
             "hruc_proposal": "warning", "blackout": "critical",
             "restoration_complete": "info", "voltage_violation": "warning",
             "voltage_unsolved": "critical"}

MAX_STEPS_PER_POLL = 24


def score_baal(sim) -> int:
    from .scoring import score_shift
    largest = max((g.p_max_mw for g in sim.world.generators
                   if g.in_service), default=0.0)
    r = score_shift(sim.traces, sim.events, sim.cfg, largest, 0.0)
    return r.get("baal", {}).get("violations", 0)


def _dcs(sim) -> list:
    from .scoring import score_shift
    largest = max((g.p_max_mw for g in sim.world.generators
                   if g.in_service), default=0.0)
    r = score_shift(sim.traces, sim.events, sim.cfg, largest,
                    sim.totals()["unserved_mwh"])
    return r.get("dcs", {}).get("reportable_events", [])


class OpsSession:
    def __init__(self, world: World, cfg: ShiftConfig, speed: float = 60.0,
                 scenario_key: str | None = None):
        self.scenario_key = scenario_key
        self.sim = OpsSimulation(world, cfg)
        self.sim.start()
        self.speed = speed              # sim-minutes per wall-minute (0=frozen)
        self._last_wall = time.monotonic()
        self._credit = 0.0
        self._alarms: list[dict] = []
        self._alarm_seen = 0
        self._absorb_alarms()

    # --- clock -------------------------------------------------------------

    def set_speed(self, speed: float) -> None:
        self._accrue()
        self.speed = max(0.0, float(speed))
        if self.speed == 0.0:
            self._credit = 0.0   # pause means pause: no coasting on backlog

    def _accrue(self) -> None:
        now = time.monotonic()
        if self.speed > 0 and not self.sim.finished:
            # speed = sim-minutes per wall-minute (60 => one 5-min step
            # every 5 wall-seconds; a 12-h shift in ~12 wall-minutes)
            sim_minutes = (now - self._last_wall) / 60.0 * self.speed
            self._credit += sim_minutes / self.sim.cfg.step_minutes
        self._last_wall = now

    def poll(self) -> dict:
        self._accrue()
        self._credit = min(self._credit, float(MAX_STEPS_PER_POLL))
        due = min(int(self._credit), MAX_STEPS_PER_POLL)
        for _ in range(due):
            if self.sim.finished:
                break
            self.sim.advance_one()
            self._credit -= 1.0
        self._absorb_alarms()
        return self.state()

    # --- alarms ------------------------------------------------------------

    def _absorb_alarms(self) -> None:
        for ev in self.sim.events[self._alarm_seen:]:
            sev = _SEVERITY.get(ev["kind"], "info")
            if sev != "info":
                self._alarms.append({
                    "id": len(self._alarms), "step": ev["step"],
                    "severity": sev, "kind": ev["kind"],
                    "text": f"{ev['kind'].replace('_', ' ')}: "
                            f"{ev.get('id', '')} {ev.get('reason', ev.get('detail', ''))}".strip(),
                    "acked": False})
        self._alarm_seen = len(self.sim.events)

    # --- state -------------------------------------------------------------

    def _sim_clock(self) -> str:
        cfg = self.sim.cfg
        minutes = cfg.start_hour * 60 + self.sim.k * cfg.step_minutes
        return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"

    def state(self) -> dict:
        sim = self.sim
        bp = {}
        if sim._basepoints:
            w = min(max(sim.k - 1, 0) - sim._window_start,
                    next(len(v) for v in sim._basepoints.values()) - 1)
            w = max(w, 0)
            bp = {g: round(float(v[min(w, len(v) - 1)])
                           + sim._agc_adjust.get(g, 0.0)
                           + sim._redispatch.get(g, 0.0), 2)
                  for g, v in sim._basepoints.items()}
        flows = getattr(sim, "last_flows", {})
        ratings = {l.id: (l.rating_emergency_mva
                          * sim._line_derate.get(l.id, 1.0),
                          l.rating_normal_mva * sim._line_derate.get(l.id, 1.0))
                   for l in sim.world.ac_lines}
        lines = [{"id": lid, "flow_mw": round(f, 1),
                  "rho_normal": round(abs(f) / max(ratings[lid][1], 1e-6), 3),
                  "rho_emergency": round(abs(f) / max(ratings[lid][0], 1e-6), 3),
                  "tripped": lid in sim._tripped_lines}
                 for lid, f in flows.items() if lid in ratings]
        lines += [{"id": lid, "flow_mw": 0.0, "rho_normal": 0.0,
                   "rho_emergency": 0.0, "tripped": True}
                  for lid in sim._tripped_lines if lid not in flows]
        lines.sort(key=lambda r: -r["rho_emergency"])
        reg_room = 0.0
        if sim.k > 0:
            reg_room = sim._regulation_headroom(sim.k - 1, sim.world, bp)
        return {
            "clock": {"step": sim.k, "n_steps": sim.cfg.n_steps,
                      "sim_time": self._sim_clock(), "speed": self.speed,
                      "finished": sim.finished},
            "traces": sim.traces,
            "events": sim.events[-40:],
            "alarms": self._alarms,
            "unacked_critical": sum(1 for a in self._alarms
                                    if a["severity"] == "critical"
                                    and not a["acked"]),
            "basepoints": bp,
            "lines": lines,
            "regulation_headroom_mw": round(reg_room, 1),
            "manual_shed_mw": sim._shed_mw,
            "redispatch": sim._redispatch,
            "out_generators": sorted(sim._out_gens),
            "eea_level": sim._eea_level,
            "sol_clocks": {lid: c * sim.cfg.step_minutes
                           for lid, c in sim._sol_clocks.items()},
            "nodal_lmps": getattr(sim, "_nodal_lmps", {}),
            "se": sim.se_result.summary()
                  if getattr(sim, "se_result", None) else None,
            "hruc_pending": getattr(sim, "_hruc_pending", None),
            "bus_voltages": {k: round(v, 4)
                             for k, v in getattr(sim, "_bus_voltages", {}).items()},
            "voltage_violations": getattr(sim, "_v_violations", []),
            "in_blackout": getattr(sim, "_blackout", False)
                           and getattr(sim, "_restored_step", None) is None,
            "da_summary": sim._da,
            "totals": sim.totals(),
        }

    # --- actions ------------------------------------------------------------

    def action(self, act: dict) -> dict:
        kind = act.get("type")
        sim = self.sim
        if sim.finished and kind not in ("ack_alarm",):
            return {"applied": False, "reason": "the shift is over — "
                    "review the report card"}
        if kind == "redispatch":
            gid, dmw = act.get("id"), float(act.get("delta_mw", 0.0))
            gen = next((g for g in sim.world.generators if g.id == gid), None)
            if gen is None:
                return {"applied": False, "reason": f"no generator '{gid}'"}
            if gid in sim._out_gens:
                return {"applied": False,
                        "reason": f"{gid} is on forced outage — you cannot "
                                  "redispatch a unit that is off the grid"}
            new = sim._redispatch.get(gid, 0.0) + dmw
            base = 0.0
            if sim._basepoints and gid in sim._basepoints:
                base = float(sim._basepoints[gid][0])
            if base + new > gen.p_max_mw + 1e-6:
                return {"applied": False,
                        "reason": f"{gid} would exceed its {gen.p_max_mw:.0f} "
                                  "MW capability — capacity is a physical "
                                  "limit, not a preference"}
            sim._redispatch[gid] = new
            sim.events.append({"step": sim.k, "kind": "operator_redispatch",
                               "id": gid, "delta_mw": dmw})
            return {"applied": True}
        if kind == "switch":
            sw_id = act.get("id", "")
            if bool(act.get("open", True)) and sw_id in sim._stuck:
                sim.events.append({"step": sim.k, "kind": "breaker_failure",
                                   "id": sw_id,
                                   "detail": "mechanism did not respond"})
                return {"applied": False,
                        "reason": f"{sw_id} failed to operate — breaker "
                                  "mechanism did not respond. Treat as a "
                                  "breaker failure: isolate via adjacent "
                                  "devices and notify maintenance"}
            sw = next((s for s in sim.world.switches if s.id == sw_id), None)
            prev = sw.open if sw is not None else None      # capture BEFORE
            res = operate_switch(sim.world, sw_id,
                                 bool(act.get("open", True)))
            if res.applied and sw is not None and sw.open != prev:
                sim._switch_ops.append((res.switch_id, prev))
                sim.events.append({"step": sim.k, "kind": "operator_switch",
                                   "id": res.switch_id, "open": res.open})
            return {"applied": res.applied, "reason": res.reason}
        if kind == "shed_load":
            mw = float(act.get("mw", 0.0))
            sim._shed_mw = max(0.0, sim._shed_mw + mw)
            sim.events.append({"step": sim.k, "kind": "operator_load_shed",
                               "mw": mw, "total_shed_mw": sim._shed_mw})
            return {"applied": True,
                    "note": "firm load interrupted — scored, but scored "
                            "better than letting protection do it for you"}
        if kind == "ack_alarm":
            aid = int(act.get("id", -1))
            for a in self._alarms:
                if a["id"] == aid:
                    a["acked"] = True
                    return {"applied": True}
            return {"applied": False, "reason": f"no alarm #{aid}"}
        if kind == "voltage":
            gid, dv = act.get("id"), float(act.get("delta_pu", 0.0))
            gen = next((g for g in sim.world.generators if g.id == gid), None)
            if gen is None:
                return {"applied": False, "reason": f"no generator '{gid}'"}
            base_v = gen.v_setpoint_pu if gen.v_setpoint_pu is not None else 1.0
            # record the original once so finish() can restore it (the shared
            # world must not carry an operator's AVR nudge into the next shift)
            sim._prev_setpoint.setdefault(gid, gen.v_setpoint_pu)
            new_v = float(np.clip(base_v + dv, 0.90, 1.10))
            gen.v_setpoint_pu = new_v
            sim.events.append({"step": sim.k, "kind": "operator_voltage",
                               "id": gid, "v_setpoint_pu": round(new_v, 3)})
            # re-check voltages so the operator sees the effect immediately
            from .topology import derive_bus_branch
            derived = derive_bus_branch(sim.world).world
            sim._voltage_check(sim.k, derived, self._last_bp())
            return {"applied": True,
                    "note": f"{gid} AVR setpoint -> {new_v:.3f} pu; "
                            "reactive dispatch adjusts voltages, not MW"}
        if kind in ("approve_hruc", "deny_hruc"):
            pending = getattr(sim, "_hruc_pending", None)
            if not pending:
                return {"applied": False, "reason": "no HRUC proposal pending"}
            sim._hruc_pending = None
            if kind == "deny_hruc":
                sim.events.append({"step": sim.k, "kind": "hruc_denied",
                                   "id": pending["unit"]})
                return {"applied": True, "note": "proposal denied — the "
                        "shortfall risk is yours to carry now"}
            gid = pending["unit"]
            if gid in sim._commitment:
                hour = (sim.k * sim.cfg.step_minutes) // 60
                sim._commitment[gid][hour:] = 1.0
            from .topology import derive_bus_branch
            sim._run_sced(sim.k, derive_bus_branch(sim.world).world)
            sim.events.append({"step": sim.k, "kind": "hruc_committed",
                               "id": gid})
            return {"applied": True,
                    "note": f"{gid} committed for the rest of the shift; "
                            "SCED re-solved with it"}
        if kind == "switching_order":
            from .switching import switching_order
            eq, phase = act.get("id", ""), act.get("phase", "isolate")
            order = switching_order(sim.world, eq, to_open=(phase == "isolate"))
            if not order:
                return {"applied": False,
                        "reason": f"no bay switches found for '{eq}' — ring "
                                  "corners isolate via their ring breakers"}
            self._active_order = {"equipment": eq, "phase": phase}
            return {"applied": True, "order": order,
                    "note": "execute in sequence with switch actions; the "
                            "interlocks will reject any step out of order"}
        if kind == "clearance":
            from .switching import switching_order
            eq = act.get("id", "")
            order = switching_order(sim.world, eq, to_open=True)
            if not order:
                return {"applied": False, "reason": f"no bay for '{eq}'"}
            if all(step["done"] for step in order):
                sim.events.append({"step": sim.k, "kind": "clearance_active",
                                   "id": eq,
                                   "detail": "isolation verified — crew may "
                                             "take the equipment"})
                return {"applied": True, "clearance": "active"}
            pending = [st["switch_id"] for st in order if not st["done"]]
            return {"applied": False,
                    "reason": "clearance requires visible isolation first — "
                              f"still closed: {', '.join(pending)}"}
        if kind == "request_sced":
            topo = None
            from .topology import derive_bus_branch
            topo = derive_bus_branch(sim.world)
            sim._run_sced(sim.k, topo.world)
            sim.events.append({"step": sim.k, "kind": "operator_sced_request"})
            return {"applied": True}
        return {"applied": False, "reason": f"unknown action type '{kind}' — "
                "valid: redispatch, switch, shed_load, ack_alarm, request_sced"}

    def _last_bp(self) -> dict:
        sim = self.sim
        if not sim._basepoints:
            return {}
        w = max(sim.k - 1 - sim._window_start, 0)
        return {g: float(v[min(w, len(v) - 1)])
                for g, v in sim._basepoints.items()}

    # --- study mode (obs.simulate analog): what-if, never mutates ----------

    def study(self, act: dict) -> dict:
        sim = self.sim
        if not sim._basepoints:
            return {"ok": False, "reason": "no dispatch yet"}
        bp = {g: float(v[0]) for g, v in sim._basepoints.items()}
        for gid, dmw in sim._redispatch.items():
            bp[gid] = max(0.0, bp.get(gid, 0.0) + dmw)
        note = ""
        saved: Optional[tuple] = None
        if act.get("type") == "redispatch":
            gid = act.get("id")
            bp[gid] = max(0.0, bp.get(gid, 0.0) + float(act.get("delta_mw", 0)))
            note = f"if {gid} moves {act.get('delta_mw')} MW"
        elif act.get("type") == "switch":
            sw = next((s for s in sim.world.switches
                       if s.id == act.get("id")), None)
            if sw is None:
                return {"ok": False, "reason": f"no switch '{act.get('id')}'"}
            saved = (sw, sw.open)
            sw.open = bool(act.get("open", True))
            note = f"if {sw.id} {'opens' if sw.open else 'closes'}"
        try:
            from .topology import derive_bus_branch
            derived = derive_bus_branch(sim.world).world
            flows = sim._dc_flows(derived, bp)
        finally:
            if saved:
                saved[0].open = saved[1]
        worst = []
        for l in derived.ac_lines:
            if l.id in flows and l.in_service:
                rho = abs(flows[l.id]) / max(l.rating_emergency_mva, 1e-6)
                worst.append({"id": l.id, "rho_emergency": round(rho, 3)})
        worst.sort(key=lambda r: -r["rho_emergency"])
        gen_total = sum(bp.values())
        return {"ok": True, "study": note,
                "worst_lines": worst[:5],
                "gen_total_mw": round(gen_total, 1),
                "would_overload": [w for w in worst if w["rho_emergency"] > 1.0]}

    def _evaluate_pass(self, t: dict) -> dict | None:
        """Did the operator meet THIS scenario's pass criterion (PRD §10)?"""
        if not self.scenario_key:
            return None
        sim = self.sim
        evs = sim.events
        key = self.scenario_key
        crit = {
            "first_shift": (t["unserved_mwh"] < 0.5,
                            "finish with ~zero unserved energy"),
            "morning_ramp": (
                score_baal(sim) == 0, "no BAAL violation"),
            "dcs_drill": (
                all(d["recovered_in_15min"] for d in
                    _dcs(sim)) and bool(_dcs(sim)),
                "ACE recovered within 15 min of the trip"),
            "thirty_minute_clock": (
                not any(e["kind"] == "sol_clock_expired" for e in evs),
                "cleared the SOL exceedance within 30 min"),
            "switching_order": (
                any(e["kind"] == "clearance_active" for e in evs),
                "clearance granted on the target line"),
            "storm_shift": (
                sim._shed_mw <= 50.0 and not t["blackout"],
                "survived turnover with minimal firm shed"),
            "blackout_restoration": (
                bool(t["blackout"]) and t.get("restoration_min") is not None,
                "restored load to 95% before turnover"),
        }.get(key)
        if crit is None:
            return None
        passed, why = crit
        return {"passed": bool(passed), "criterion": why}

    def inject(self, event: dict) -> dict:
        """Instructor console: schedule an event into the running shift."""
        allowed = {"trip_generator", "derate_line", "scale_load", "stick_breaker", "bad_meter"}
        kind = event.get("kind")
        if kind not in allowed:
            return {"applied": False,
                    "reason": f"instructor can inject {sorted(allowed)}"}
        ev = dict(event)
        ev["step"] = int(ev.get("step", self.sim.k + 1))
        if ev["step"] <= self.sim.k:
            ev["step"] = self.sim.k + 1
        self.sim.cfg.scripted_events.append(ev)
        return {"applied": True, "scheduled_step": ev["step"]}

    def report(self) -> dict:
        from .scoring import score_shift

        sim = self.sim
        t = sim.totals()
        largest = max((g.p_max_mw for g in sim.world.generators
                       if g.in_service), default=0.0)
        nerc = score_shift(sim.traces, sim.events, sim.cfg, largest,
                           t["unserved_mwh"])
        grades = dict(nerc.get("grades", {}))
        grades["security"] = "A" if t["line_trips"] == 0 else "C"
        expired = sum(1 for e in sim.events
                      if e["kind"] == "sol_clock_expired")
        grades["sol_compliance_top001"] = "A" if expired == 0 else "C"
        return {
            "finished": sim.finished,
            "steps_completed": sim.k,
            "totals": t,
            "grades": grades,
            "nerc": nerc,
            "eea_peak": max((e.get("eea_level", 0) for e in sim.events
                             if e["kind"] == "rc_directive"), default=0),
            "scenario": self.scenario_key,
            "scenario_pass": self._evaluate_pass(t),
            "voltage_note": (f"{t['voltage_violations']} voltage-schedule "
                             "excursions" if t.get("voltage_violations")
                             else "voltages held to schedule"),
            "note": "graded as NERC grades desks: CPS1 (frequency support), "
                    "BAAL (ACE limits), DCS (15-min recovery), TOP-001 "
                    "(30-min SOL clocks), plus unserved energy and trips",
        }
