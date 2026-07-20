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
from typing import Optional

from ..schema import World
from .kernel import OpsSimulation, ShiftConfig
from .switching import operate_switch

_SEVERITY = {"line_trip": "critical", "generator_trip": "critical",
             "overload_warning": "warning", "sced_failed": "critical",
             "line_derated": "warning", "load_scaled": "info",
             "line_reclosed": "info", "turnover_briefing": "info"}

MAX_STEPS_PER_POLL = 24


class OpsSession:
    def __init__(self, world: World, cfg: ShiftConfig, speed: float = 60.0):
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
            sim_minutes = (now - self._last_wall) / 60.0 * self.speed * 60.0
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
            res = operate_switch(sim.world, act.get("id", ""),
                                 bool(act.get("open", True)))
            if res.applied:
                sw = next(s for s in sim.world.switches if s.id == res.switch_id)
                sim._switch_ops.append((res.switch_id, not sw.open))
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
        if kind == "request_sced":
            topo = None
            from .topology import derive_bus_branch
            topo = derive_bus_branch(sim.world)
            sim._run_sced(sim.k, topo.world)
            sim.events.append({"step": sim.k, "kind": "operator_sced_request"})
            return {"applied": True}
        return {"applied": False, "reason": f"unknown action type '{kind}' — "
                "valid: redispatch, switch, shed_load, ack_alarm, request_sced"}

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

    def report(self) -> dict:
        t = self.sim.totals()
        def grade(ok, mid, val, reverse=False):
            v = -val if reverse else val
            return "A" if v <= ok else ("B" if v <= mid else "C")
        return {
            "finished": self.sim.finished,
            "steps_completed": self.sim.k,
            "totals": t,
            "grades": {
                "reliability": grade(0.0, 5.0, t["unserved_mwh"]),
                "frequency_control": grade(0.02, 0.05, t["max_freq_dev_hz"]),
                "security": grade(0, 1, t["line_trips"]),
            },
            "note": "full CPS1/BAAL/DCS scoring arrives with Phase 2; these "
                    "grades cover unserved energy, frequency control, and "
                    "line security only",
        }
