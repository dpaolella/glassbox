"""Planner challenges (issue #30): goal-driven scenarios with scoring.

Each challenge grades the CURRENT world — including any proposals the user
placed in build mode — by running the real engines and comparing outcomes to
targets. The grade explanation uses the same numbers the engines produced, so
losing teaches as much as winning.
"""

from __future__ import annotations

from typing import Any

from .scenario import (
    Layer,
    Override,
    Scenario,
    SpatialOperator,
    run_scenario,
    world_with_builds,
)
from .scenario.runner import _clone_world
from .schema import World

CHALLENGES: list[dict[str, Any]] = [
    {
        "key": "clean_80",
        "name": "80% clean by design",
        "brief": ("Policy just jumped the clean-energy standard to 80%. Reach "
                  "the highest clean share you can without shedding load. "
                  "Place extra wind/solar/storage proposals in build mode if "
                  "the default options aren't enough."),
        "grades": "gold ≥ 75% VRE · silver ≥ 65% · bronze ≥ 55%, all with no unserved energy",
    },
    {
        "key": "ride_the_lull",
        "name": "Ride the dunkelflaute",
        "brief": ("Plan a build-out (the CEM picks among all proposals, "
                  "including yours), commit it, then survive the worst "
                  "dark-doldrums window in the weather ensemble. Storage and "
                  "firm capacity are your friends."),
        "grades": "gold: 0 MWh unserved · silver < 500 · bronze < 2000",
    },
    {
        "key": "retire_coal",
        "name": "Retire the coal fleet",
        "brief": ("The coal unit retires TODAY, not in 2034. Plan replacement "
                  "capacity, commit it, and keep the peak-hour power flow "
                  "N-1 secure."),
        "grades": "gold: no unserved & N-1 secure · silver: ≤ 2 violations · bronze: ≤ 5",
    },
]


def _medal(score: float) -> str:
    return "gold" if score >= 90 else "silver" if score >= 70 \
        else "bronze" if score >= 50 else "none"


def _cem(world: World, overrides: list[Override] | None = None,
         spatial: str = "identity"):
    return run_scenario(world, Scenario(
        id="challenge_cem", layer=Layer.CEM,
        spatial_operator=SpatialOperator(spatial),
        temporal_map_id="representative_days", weather_years=[0],
        n_rep_days=4, overrides=overrides or []))


def score_challenge(world: World, key: str) -> dict[str, Any]:
    if key == "clean_80":
        run = _cem(world, overrides=[Override(kind="set_policy",
                                              policy_kind="rps", value=0.8)])
        vre = float(run.summary.get("vre_penetration") or 0.0)
        unserved = float(run.summary.get("unserved_mwh_weighted") or 0.0)
        served_ok = unserved < 1.0
        score = min(100.0, (vre / 0.75) * 100.0) if served_ok else 25.0 * vre / 0.75
        return {
            "key": key, "score": round(score, 1), "medal": _medal(score),
            "metrics": {
                "vre_share": round(vre, 3),
                "unserved_mwh": round(unserved, 1),
                "total_cost": round(run.summary.get("total_cost", 0.0)),
                "curtailment_mwh": round(
                    run.summary.get("curtailment_mwh_weighted", 0.0)),
                "built": run.summary.get("built_resource_potential_mw", {}),
            },
            "explanation": (
                f"The expansion reached a {vre:.0%} clean-energy share"
                + ("" if served_ok else
                   f" but shed {unserved:,.0f} MWh/yr — a plan that drops load "
                   "doesn't count") +
                ". Anything short of 80% pays the compliance payment; check "
                "curtailment — clean energy you throw away doesn't count "
                "toward the share."),
        }

    if key == "ride_the_lull":
        from .weather.events import detect_events

        events = detect_events(world)
        lull = next((e for e in events if e["kind"] == "dunkelflaute"), None)
        if lull is None:
            return {"key": key, "score": 0, "medal": "none",
                    "metrics": {}, "explanation": "no dunkelflaute detected"}
        plan = _cem(world)
        committed = world_with_builds(world, plan.result)
        op = run_scenario(committed, Scenario(**{
            **lull["scenario"], "id": "challenge_lull"}))
        unserved = float(op.summary.get("unserved_mwh_weighted") or 0.0)
        score = 100.0 if unserved < 1 else 75.0 if unserved < 500 \
            else 55.0 if unserved < 2000 else max(0.0, 40.0 - unserved / 500)
        return {
            "key": key, "score": round(score, 1), "medal": _medal(score),
            "metrics": {
                "event": lull["name"],
                "unserved_mwh": round(unserved, 1),
                "avg_price": round(op.summary.get("avg_price", 0.0), 1),
                "committed_builds": {
                    **plan.result.built_resource_potential_mw,
                    **plan.result.built_storage_power_mw,
                    **plan.result.built_transmission_mw,
                },
            },
            "explanation": (
                f"Your committed build-out faced {lull['name']} "
                f"({lull['duration_h'] // 24} days of "
                f"{lull['severity']:.0%} average VRE availability). "
                + ("Every megawatt-hour was served — the lights stayed on."
                   if unserved < 1 else
                   f"{unserved:,.0f} MWh went unserved. The lull outlasts "
                   "4-hour batteries; firm capacity or longer storage is "
                   "what survives it.")),
        }

    if key == "retire_coal":
        w = _clone_world(world)
        retired = []
        for g in w.generators:
            if g.technology.value == "coal" and g.in_service:
                g.in_service = False
                retired.append(g.id)
        plan = _cem(w)
        committed = world_with_builds(w, plan.result)
        pf = run_scenario(committed, Scenario(
            id="challenge_pf", layer=Layer.PF, weather_years=[0]))
        unserved = float(plan.summary.get("unserved_mwh_weighted") or 0.0)
        n_viol = int(pf.summary.get("n_n1_contingencies_with_violations") or 0)
        score = (100.0 if unserved < 1 and n_viol == 0
                 else 75.0 if n_viol <= 2 and unserved < 100
                 else 55.0 if n_viol <= 5 else 30.0)
        return {
            "key": key, "score": round(score, 1), "medal": _medal(score),
            "metrics": {
                "retired": retired,
                "unserved_mwh": round(unserved, 1),
                "n1_violations": n_viol,
                "replacement_builds": {
                    **plan.result.built_resource_potential_mw,
                    **plan.result.built_capacity_mw,
                    **plan.result.built_storage_power_mw,
                },
                "pf_converged": pf.summary.get("converged"),
            },
            "explanation": (
                f"Coal ({', '.join(retired) or 'none found'}) is gone. The "
                "expansion replaced it and the committed system was stress-"
                "tested with N-1 power flow at the peak hour: "
                + ("fully secure — every single-element outage stays within "
                   "limits." if n_viol == 0 else
                   f"{n_viol} post-contingency violation(s) remain — the "
                   "replacement sits in the wrong place electrically, even "
                   "if the energy balances.")),
        }

    raise KeyError(key)
