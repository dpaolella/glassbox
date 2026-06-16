"""Scenario diffing (PRD Section 9.5).

Given two scenario runs, produce a side-by-side diff of inputs and results:
capacity mix, curtailment, prices, reliability. This is how every lesson is
presented (nodal vs zonal, one year vs many, with vs without a carbon price).
"""

from __future__ import annotations

from typing import Any

from .runner import ScenarioRun


def _diff_scalar(a: Any, b: Any) -> dict[str, Any]:
    out = {"a": a, "b": b}
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        out["delta"] = round(b - a, 4)
        out["pct"] = round((b - a) / a * 100, 2) if a else None
    return out


def _diff_dict(a: dict, b: dict) -> dict[str, Any]:
    keys = sorted(set(a) | set(b))
    return {k: _diff_scalar(a.get(k, 0.0), b.get(k, 0.0)) for k in keys}


def diff_runs(run_a: ScenarioRun, run_b: ScenarioRun) -> dict[str, Any]:
    sa, sb = run_a.summary, run_b.summary
    diff: dict[str, Any] = {
        "a": {"id": run_a.scenario.id, "name": run_a.scenario.name,
              "layer": sa.get("layer"), "spatial": sa.get("spatial"),
              "weather_years": sa.get("weather_years")},
        "b": {"id": run_b.scenario.id, "name": run_b.scenario.name,
              "layer": sb.get("layer"), "spatial": sb.get("spatial"),
              "weather_years": sb.get("weather_years")},
        "scalars": {},
        "capacity_mix_mw": _diff_dict(sa.get("capacity_mix_mw", {}),
                                      sb.get("capacity_mix_mw", {})),
    }
    for key in ("total_cost", "vre_penetration", "curtailment_mwh_weighted",
                "unserved_mwh_weighted", "avg_price", "price_spread",
                "lole_hours_per_year", "eue_mwh_per_year",
                "losses_mw", "n_base_overloads",
                "n_n1_contingencies_with_violations"):
        if key in sa or key in sb:
            diff["scalars"][key] = _diff_scalar(sa.get(key, 0.0), sb.get(key, 0.0))

    if "elcc_mw" in sa or "elcc_mw" in sb:
        diff["elcc_mw"] = _diff_dict(sa.get("elcc_mw", {}), sb.get("elcc_mw", {}))

    if "nodal_prices" in sa or "nodal_prices" in sb:
        diff["nodal_prices"] = _diff_dict(sa.get("nodal_prices", {}),
                                          sb.get("nodal_prices", {}))
    if "congestion" in sa or "congestion" in sb:
        diff["congestion"] = _diff_dict(sa.get("congestion", {}),
                                        sb.get("congestion", {}))
    if "realized_capacity_factor" in sa or "realized_capacity_factor" in sb:
        diff["realized_capacity_factor"] = _diff_dict(
            sa.get("realized_capacity_factor", {}),
            sb.get("realized_capacity_factor", {}))
    return diff
