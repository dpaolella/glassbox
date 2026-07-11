"""Multi-year planning study (issue #33): myopic rolling capacity expansion.

Real planning is a movie, not a snapshot. Each stage: demand grows, plants
past their retirement year age out, the CEM decides what to build for *that*
stage, and the builds are committed into the world the next stage inherits
(via the build->operate pipeline, issue #9). Myopic chaining — each stage
optimizes only itself — is deliberately imperfect: path dependency is part of
the lesson (a perfect-foresight multi-period LP would choose differently).
"""

from __future__ import annotations

from typing import Any

from ..schema import World
from .builds import world_with_builds
from .runner import _clone_world, run_scenario
from .scenario import Layer, Scenario, SpatialOperator


def run_planning_study(
    world: World,
    *,
    start_year: int = 2026,
    n_stages: int = 4,
    years_per_stage: int = 4,
    growth_per_year: float = 0.02,
    spatial: str = "aggregate",
    n_rep_days: int = 4,
) -> dict[str, Any]:
    """Chain CEM stages: grow load, retire aged plants, build, commit, repeat."""
    w = _clone_world(world)
    stages: list[dict[str, Any]] = []

    for i in range(n_stages):
        year = start_year + i * years_per_stage
        # load growth compounds from the study start
        w.demand_scale = (1.0 + growth_per_year) ** (year - start_year)

        # age-based retirements (honored by every engine since issue #5)
        retired = []
        for g in w.generators:
            if (g.in_service and g.retirement_year is not None
                    and g.retirement_year <= year):
                g.in_service = False
                retired.append({"id": g.id, "name": g.name,
                                "p_max_mw": g.p_max_mw})

        run = run_scenario(w, Scenario(
            id=f"plan_{year}", layer=Layer.CEM,
            spatial_operator=SpatialOperator(spatial),
            temporal_map_id="representative_days",
            weather_years=[min(i, 9)],  # walk the weather ensemble too
            n_rep_days=n_rep_days))
        res = run.result

        stages.append({
            "year": year,
            "demand_scale": round(w.demand_scale, 4),
            "retired": retired,
            "built_capacity_mw": {k: round(v, 1)
                                  for k, v in res.built_capacity_mw.items()},
            "built_storage_power_mw": {k: round(v, 1)
                                       for k, v in res.built_storage_power_mw.items()},
            "built_transmission_mw": {k: round(v, 1)
                                      for k, v in res.built_transmission_mw.items()},
            "built_resource_potential_mw": {
                k: round(v, 1)
                for k, v in res.built_resource_potential_mw.items()},
            "capacity_mix_mw": run.summary.get("capacity_mix_mw", {}),
            "total_cost": run.summary.get("total_cost", 0.0),
            "vre_penetration": run.summary.get("vre_penetration"),
            "curtailment_mwh_weighted": run.summary.get("curtailment_mwh_weighted"),
            "unserved_mwh_weighted": run.summary.get("unserved_mwh_weighted"),
        })

        # the next stage inherits this stage's decisions as real assets
        w = world_with_builds(w, res)

    return {
        "start_year": start_year,
        "years_per_stage": years_per_stage,
        "growth_per_year": growth_per_year,
        "spatial": spatial,
        "stages": stages,
        "note": ("Myopic rolling study: each stage optimizes itself and commits "
                 "its builds before the next begins. Path dependency is real — "
                 "and part of the lesson."),
    }
