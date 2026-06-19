"""Scenario framework + diff tests (PRD Sections 10, 9.5)."""

from __future__ import annotations

import warnings

import pytest

from glassbox.scenario import (
    Layer,
    Override,
    Scenario,
    SpatialOperator,
    apply_overrides,
    diff_runs,
    run_scenario,
)
from glassbox.world import build_default_world_with_weather

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


def test_overrides_do_not_mutate_original(world):
    n_before = len(world.policies)
    ov = [Override(kind="set_policy", policy_kind="carbon_price", value=99.0)]
    w2 = apply_overrides(world, ov)
    # the clone shares the time-series arrays but is otherwise independent
    assert w2.time_series_store is world.time_series_store
    carbon = next(p for p in w2.policies if p.kind.value == "carbon_price")
    assert carbon.value == 99.0
    orig = next(p for p in world.policies if p.kind.value == "carbon_price")
    assert orig.value == 0.0
    assert len(world.policies) == n_before


def test_set_field_override_retires_unit(world):
    gid = next(g.id for g in world.generators if not g.is_candidate)
    w2 = apply_overrides(world, [Override(kind="set_field", collection="generators",
                                          id=gid, field="in_service", value=False)])
    assert next(g for g in w2.generators if g.id == gid).in_service is False
    assert next(g for g in world.generators if g.id == gid).in_service is True


def test_nodal_vs_zonal_diff(world):
    def cem(mode):
        return run_scenario(world, Scenario(
            id=f"cem_{mode}", layer=Layer.CEM, spatial_operator=SpatialOperator(mode),
            temporal_map_id="representative_days", weather_years=[0], n_rep_days=3))

    d = diff_runs(cem("aggregate"), cem("identity"))
    assert "capacity_mix_mw" in d
    assert "total_cost" in d["scalars"]
    # nodal vs zonal should differ in curtailment
    assert "curtailment_mwh_weighted" in d["scalars"]


def test_one_year_vs_many_diff(world):
    """Same world, weather_years differs — the multi-weather-year lesson."""
    def cem(years):
        return run_scenario(world, Scenario(
            id="cem", layer=Layer.CEM, spatial_operator=SpatialOperator.AGGREGATE,
            temporal_map_id="representative_days", weather_years=years, n_rep_days=3))

    one = cem([0])
    many = cem([0, 1, 2])
    d = diff_runs(one, many)
    # the realized capacity factors (and hence the build) shift between the two
    assert d["a"]["weather_years"] == [0]
    assert d["b"]["weather_years"] == [0, 1, 2]
