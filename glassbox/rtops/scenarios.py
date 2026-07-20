"""The shift scenario library (issue #56 Phase 2, PRD §10).

Five seeded, replayable shifts. Scenario 6 (the switching order + stuck
breaker) arrives with the clearance machinery. Each entry is a ShiftConfig
factory plus the lesson and pass criteria the challenge UI shows.
"""

from __future__ import annotations

from .kernel import ShiftConfig

SCENARIOS: dict[str, dict] = {
    "first_shift": {
        "name": "First shift (tutorial)",
        "lesson": "A benign day. Learn the desk: frequency, ACE, reserves, "
                  "line loadings, and the event log. Nothing should break — "
                  "watch the morning ramp arrive and the market re-solve "
                  "every hour.",
        "pass": "finish the shift with zero unserved energy",
        "config": lambda: ShiftConfig(seed=101, n_steps=144,
                                      load_error_sigma=0.005,
                                      forced_outages=False),
    },
    "morning_ramp": {
        "name": "The morning ramp",
        "lesson": "Load was under-forecast and the committed fleet is "
                  "sluggish. Watch ACE sag as actuals outrun basepoints — "
                  "redispatch ahead of the ramp, not behind it.",
        "pass": "no BAAL violation through the shift",
        "config": lambda: ShiftConfig(
            seed=202, n_steps=72, load_error_sigma=0.02,
            forced_outages=False,
            scripted_events=[{"step": 12, "kind": "scale_load",
                              "factor": 1.06}]),
    },
    "dcs_drill": {
        "name": "DCS drill",
        "lesson": "Your largest unit trips without warning at 06:15. "
                  "BAL-002 gives you 15 minutes to recover ACE. Deploy "
                  "reserves (redispatch up), then rebuild headroom.",
        "pass": "ACE recovered within 15 minutes of the trip (DCS pass)",
        "config": lambda: ShiftConfig(
            seed=303, n_steps=72, forced_outages=False,
            scripted_events=[{"step": 15, "kind": "trip_generator",
                              "id": "coal_1", "repair_steps": 48}]),
    },
    "thirty_minute_clock": {
        "name": "The 30-minute clock",
        "lesson": "A line derate puts the system one contingency away from "
                  "an overload. RTCA flags it and the SOL clock starts — "
                  "clear it by redispatch before TOP-001's 30 minutes run "
                  "out.",
        "pass": "no sol_clock_expired event",
        "config": lambda: ShiftConfig(
            seed=404, n_steps=48, forced_outages=False,
            scripted_events=[{"step": 6, "kind": "derate_line",
                              "id": "", "factor": 0.55}]),  # id filled at start
    },
    "switching_order": {
        "name": "The switching order",
        "lesson": "Walk a real clearance: open the line's breakers, then its "
                  "disconnectors (the interlocks enforce the order), and "
                  "request the clearance. Mid-shift, a breaker mechanism "
                  "fails during a fault — protection clears the whole busbar "
                  "section. Recognize the breaker-failure signature and "
                  "re-serve the lost section.",
        "pass": "clearance granted on the target line; after the breaker "
                "failure, restore service (reclose the cleared section)",
        "config": lambda: ShiftConfig(
            seed=606, n_steps=60, forced_outages=False,
            stuck_breakers=[],           # armed by the scripted event below
            scripted_events=[
                {"step": 10, "kind": "stick_breaker", "id": ""},
                {"step": 30, "kind": "derate_line", "id": "", "factor": 0.3}]),
    },
    "blackout_restoration": {
        "name": "Blackout & restoration",
        "lesson": "A cascade takes most of the load. Once served load "
                  "collapses the system is in blackout — your job flips to "
                  "restoration (EOP-005): units re-commit, cleared lines "
                  "reclose on their timers, and load comes back in blocks. "
                  "Watch the served-fraction trace climb back.",
        "pass": "restore load to 95% before turnover",
        "config": lambda: ShiftConfig(
            seed=707, n_steps=48, forced_outages=False,
            scripted_events=[
                {"step": 3, "kind": "trip_generator", "id": "nuclear_1"},
                {"step": 4, "kind": "trip_generator", "id": "ccgt_4"},
                {"step": 5, "kind": "scale_load", "factor": 1.15}]),
    },
    "storm_shift": {
        "name": "Storm shift",
        "lesson": "High forecast error, elevated outage risk, and two "
                  "scripted trips as the front passes. Shed proactively if "
                  "you must — protection shedding for you scores worse.",
        "pass": "survive to turnover with minimal firm load shed",
        "config": lambda: ShiftConfig(
            seed=505, n_steps=144, load_error_sigma=0.035,
            vre_error_sigma=0.06, forced_outages=True,
            scripted_events=[
                {"step": 40, "kind": "trip_generator", "id": "ccgt_1",
                 "repair_steps": 30},
                {"step": 70, "kind": "scale_load", "factor": 1.04}]),
    },
}


def scenario_list() -> list[dict]:
    return [{"id": key, "name": sc["name"], "lesson": sc["lesson"],
             "pass": sc["pass"]} for key, sc in SCENARIOS.items()]


def scenario_config(key: str, world) -> ShiftConfig:
    cfg = SCENARIOS[key]["config"]()
    # late-bind asset ids that depend on the world
    for ev in cfg.scripted_events:
        if ev.get("kind") == "derate_line" and not ev.get("id"):
            ev["id"] = max(world.ac_lines,
                           key=lambda l: l.rating_normal_mva).id
        if ev.get("kind") == "trip_generator" and \
                not any(g.id == ev["id"] for g in world.generators):
            ev["id"] = max(world.generators, key=lambda g: g.p_max_mw).id
        if ev.get("kind") == "stick_breaker" and not ev.get("id"):
            # arm the stuck mechanism on one end-breaker of the derated line
            target = max(world.ac_lines, key=lambda l: l.rating_normal_mva).id
            ev["id"] = f"cb__{target}__1"
    return cfg
