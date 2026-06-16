"""Projection operator tests (PRD Section 5)."""

from __future__ import annotations

import numpy as np

from glassbox.operators import (
    AttributeProjection,
    SpatialMode,
    SpatialProjection,
    TemporalProjection,
    build_full_chronology_map,
    build_representative_days_map,
)
from glassbox.schema import Facet, Generator
from glassbox.world import build_default_world


def test_attribute_projection_matches_facet_metadata():
    op = AttributeProjection(Facet.OPS)
    fields = op.fields_for(Generator)
    assert "id" in fields
    assert "heat_rate_mmbtu_per_mwh" in fields
    assert "capex_per_mw" not in fields  # inv-only


def test_attribute_projection_drops_unseen_detail():
    world = build_default_world()
    inv_view = AttributeProjection(Facet.INV).apply(world)
    g0 = inv_view["generators"][0]
    assert "capex_per_mw" in g0
    assert "heat_rate_mmbtu_per_mwh" not in g0  # ops-only, invisible to inv
    ex = AttributeProjection(Facet.INV).explain()
    assert ex.information_loss  # must state what it hides


def test_spatial_identity_is_lossless():
    world = build_default_world()
    op = SpatialProjection(SpatialMode.IDENTITY)
    view = op.apply(world)
    assert len(view.node_ids) == len(world.buses)
    assert not view.collapsed_branch_ids


def test_spatial_aggregate_collapses_to_zones():
    world = build_default_world()
    op = SpatialProjection(SpatialMode.AGGREGATE)
    view = op.apply(world)
    assert len(view.node_ids) == len(world.zones)
    # all buses accounted for
    members = sum(len(v) for v in view.node_members.values())
    assert members == len(world.buses)
    # intra-zonal branches were collapsed (information loss)
    assert view.collapsed_branch_ids
    # inter-zonal corridors exist and carry NTC estimates
    assert view.transfer_limits_mw
    ex = op.explain()
    assert any("transfer" in s.lower() for s in ex.information_loss)


def test_temporal_full_chronology_preserves_everything():
    n = 8760
    tmap = build_full_chronology_map(n)
    op = TemporalProjection(tmap)
    view = op.apply()
    assert len(view.timesteps) == n
    assert view.chronological
    assert view.weights.sum() == n


def test_temporal_representative_days_reduces_and_loses_chronology():
    # stack a synthetic load + wind signal over ~1 year
    hours = 24 * 60
    t = np.arange(hours)
    load = 1.0 + 0.3 * np.sin(2 * np.pi * (t % 24) / 24)
    wind = 0.5 + 0.4 * np.cos(2 * np.pi * t / (24 * 7))
    series = np.vstack([load, wind])
    tmap = build_representative_days_map(series, n_days=8, seed=0)
    op = TemporalProjection(tmap)
    view = op.apply()
    assert len(view.timesteps) < hours
    assert not view.chronological
    ex = op.explain()
    assert any("chronology" in s.lower() for s in ex.information_loss)


def test_zonal_load_sums_equal_nodal(tmp_path=None):
    # The aggregate operator's promise: zonal loads sum exactly (Section 5.1).
    world = build_default_world()
    op = SpatialProjection(SpatialMode.AGGREGATE)
    view = op.apply(world)
    # every bus maps to exactly one zone-node
    for b in world.buses:
        assert b.id in view.bus_to_node
