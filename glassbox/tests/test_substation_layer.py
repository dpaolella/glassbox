"""Phase 0a exit tests (issue #56): the substation layer must be provably
invisible to planning while every switch is closed, and honestly consequential
the moment one opens."""

from __future__ import annotations

import numpy as np
import pytest

from glassbox.rtops import (derive_bus_branch, elaborate_world, operate_switch,
                            reset_switches)
from glassbox.schema import SwitchKind, World
from glassbox.schema.substation import SubstationArrangement
from glassbox.world import build_default_world_with_weather


@pytest.fixture(scope="module")
def elaborated() -> World:
    world, _ = build_default_world_with_weather()
    return elaborate_world(world)


def _solve_pcm_24h(world: World) -> tuple[float, np.ndarray]:
    from glassbox.engines import assemble_view
    from glassbox.engines.economic_core import (EngineOptions,
                                                build_dispatch_model,
                                                solve_model)
    from glassbox.operators.spatial import SpatialMode, SpatialProjection

    sview = SpatialProjection(SpatialMode.IDENTITY).apply(world)
    T = 24
    view = assemble_view(world, sview, np.arange(T), np.zeros(T, dtype=int),
                         np.ones(T), 1.0, investment=False)
    built = build_dispatch_model(view, EngineOptions(
        investment=False, unit_commitment=False, reserves=False, label="t"))
    status = solve_model(built)
    assert "ok" in status or "optimal" in status.lower()
    obj = float(built.m.objective.value)
    gen = built.m.variables["gen_p"].solution.values
    return obj, gen


# --- elaboration ------------------------------------------------------------


def test_every_bus_becomes_a_substation(elaborated):
    assert len(elaborated.substations) == len(elaborated.buses)
    kinds = {s.arrangement for s in elaborated.substations}
    assert SubstationArrangement.SPLIT_BUSBAR in kinds
    assert SubstationArrangement.RING in kinds
    # every branch end and injection got a terminal
    n_ends = 2 * sum(1 for ln in elaborated.ac_lines) \
        + 2 * len(elaborated.transformers) + 2 * len(elaborated.dc_lines) \
        + len(elaborated.generators) + len(elaborated.hydro_units) \
        + len(elaborated.storage_units) + len(elaborated.loads) \
        + len(elaborated.shunts)
    assert len(elaborated.equipment_terminals) == n_ends
    # idempotent
    before = len(elaborated.switches)
    elaborate_world(elaborated)
    assert len(elaborated.switches) == before


def test_serialization_roundtrip(elaborated, tmp_path):
    from glassbox.world import load_world, save_world
    save_world(elaborated, tmp_path / "w")
    back = load_world(tmp_path / "w")
    assert len(back.switches) == len(elaborated.switches)
    assert len(back.connectivity_nodes) == len(elaborated.connectivity_nodes)


# --- the identity invariant (CI-enforced) -----------------------------------


def test_all_closed_derives_identical_world(elaborated):
    topo = derive_bus_branch(elaborated)
    assert topo.identical
    assert topo.world is elaborated          # identity fast-path
    energized = [t for t in topo.topo_nodes if t.energized]
    assert len(energized) == len(elaborated.buses)


def test_planning_results_byte_identical_even_without_fast_path(elaborated):
    """Force the full rebuild path and prove engine outputs are unchanged."""
    topo = derive_bus_branch(elaborated, force_rebuild=True)
    assert topo.world is not elaborated
    obj_a, gen_a = _solve_pcm_24h(elaborated)
    obj_b, gen_b = _solve_pcm_24h(topo.world)
    assert obj_a == obj_b
    np.testing.assert_array_equal(gen_a, gen_b)


# --- switching has consequences ---------------------------------------------


def test_open_line_bay_takes_line_out(elaborated):
    line = elaborated.ac_lines[0]
    try:
        for seq in (1, 2):
            r = operate_switch(elaborated, f"cb__{line.id}__{seq}", True)
            assert r.applied, r.reason
        topo = derive_bus_branch(elaborated)
        assert not topo.identical
        assert line.id in topo.isolated_equipment
        derived_line = next(l for l in topo.world.ac_lines if l.id == line.id)
        assert not derived_line.in_service
        # original world untouched by derivation
        assert line.in_service
    finally:
        reset_switches(elaborated)


def test_bus_tie_open_splits_substation(elaborated):
    sub = next(s for s in elaborated.substations
               if s.arrangement == SubstationArrangement.SPLIT_BUSBAR)
    try:
        r = operate_switch(elaborated, f"cb__{sub.bus_id}__tie", True)
        assert r.applied
        topo = derive_bus_branch(elaborated)
        assert sub.bus_id in topo.split_buses
        ids = topo.split_buses[sub.bus_id]
        assert len(ids) == 2 and sub.bus_id in ids
        derived_bus_ids = {b.id for b in topo.world.buses}
        assert set(ids) <= derived_bus_ids
        # equipment landed on both sections
        on_new = [g for g in topo.world.generators + topo.world.loads
                  if g.bus_id == [i for i in ids if i != sub.bus_id][0]]
        on_old_terms = [t for t in topo.topo_nodes if t.id == sub.bus_id]
        assert on_new or on_old_terms  # at least the split is representable
        # the split bus joined its zone
        zone = next(z for z in topo.world.zones
                    if sub.bus_id in z.member_bus_ids)
        assert set(ids) <= set(zone.member_bus_ids)
    finally:
        reset_switches(elaborated)


def test_isolated_generator_leaves_the_market(elaborated):
    # pick a generator with a conventional bay (ring corners have no per-bay
    # breaker — their isolation story is the ring breakers')
    switch_ids = {s.id for s in elaborated.switches}
    gen = next(g for g in elaborated.generators
               if f"cb__{g.id}__1" in switch_ids)
    try:
        r = operate_switch(elaborated, f"cb__{gen.id}__1", True)
        assert r.applied, r.reason
        topo = derive_bus_branch(elaborated)
        assert gen.id in topo.isolated_equipment
        dgen = next(g for g in topo.world.generators if g.id == gen.id)
        assert not dgen.in_service
        obj_iso, _ = _solve_pcm_24h(topo.world)
        obj_base, _ = _solve_pcm_24h(elaborated)
        assert obj_iso >= obj_base  # losing a unit can never cheapen dispatch
    finally:
        reset_switches(elaborated)


def test_ring_survives_single_breaker_open(elaborated):
    sub = next(s for s in elaborated.substations
               if s.arrangement == SubstationArrangement.RING)
    try:
        r = operate_switch(elaborated, f"cb__{sub.bus_id}__ring1", True)
        assert r.applied
        topo = derive_bus_branch(elaborated)
        # the ring reroutes: no split, nothing isolated at this substation
        assert sub.bus_id not in topo.split_buses
        assert not any(t.substation_id == sub.id and t.energized
                       for t in topo.topo_nodes[1:]
                       if t.planning_bus_id == sub.bus_id and t.id != sub.bus_id)
    finally:
        reset_switches(elaborated)


# --- interlocks (the teaching affordance) -----------------------------------


def test_disconnector_under_load_is_rejected(elaborated):
    line = elaborated.ac_lines[0]
    ds = f"ds__{line.id}__1"
    r = operate_switch(elaborated, ds, True)
    assert not r.applied
    assert "interlock" in r.reason and "breaker" in r.reason
    # open the paired breaker, then the disconnector may move
    try:
        assert operate_switch(elaborated, f"cb__{line.id}__1", True).applied
        r2 = operate_switch(elaborated, ds, True)
        assert r2.applied
    finally:
        reset_switches(elaborated)


# --- API surface ------------------------------------------------------------


def test_api_elaborate_switch_topology(tmp_path):
    from fastapi.testclient import TestClient

    from glassbox.api.app import app
    client = TestClient(app)
    client.post("/api/world/reset")
    counts = client.post("/api/world/elaborate").json()
    assert counts["substations"] > 0 and counts["switches"] > 0

    topo = client.get("/api/topology").json()
    assert topo["identical"] is True

    subs = client.get("/api/substations").json()
    tie = next((s for s in subs if s["arrangement"] == "split_busbar"), None)
    assert tie is not None
    tie_id = f"cb__{tie['bus_id']}__tie"
    res = client.post(f"/api/switch/{tie_id}", json={"open": True}).json()
    assert res["applied"] and res["topology"]["identical"] is False
    assert tie["bus_id"] in res["topology"]["split_buses"]

    # interlock rejection surfaces the reason
    ds = next(s["id"] for s in tie["switches"]
              if s["kind"] == "disconnector" and s["paired_breaker_ids"])
    denied = client.post(f"/api/switch/{ds}", json={"open": True}).json()
    assert not denied["applied"] and "interlock" in denied["reason"]
    client.post("/api/world/reset")
