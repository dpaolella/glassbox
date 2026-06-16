"""Default seed-system property tests (PRD Section 8)."""

from __future__ import annotations

from glassbox.schema import GenTechnology, InterfaceLimitSource
from glassbox.world import build_default_world, build_default_world_with_weather


def test_bus_count_in_range():
    world = build_default_world()
    assert 24 <= len(world.buses) <= 30


def test_three_zones():
    world = build_default_world()
    assert len(world.zones) == 3
    # every bus belongs to exactly one zone
    seen = set()
    for z in world.zones:
        for b in z.member_bus_ids:
            assert b not in seen
            seen.add(b)
    assert seen == {b.id for b in world.buses}


def test_binding_interzonal_interface_exists():
    world = build_default_world()
    assert world.interfaces
    iface = world.interfaces[0]
    assert iface.member_line_ids
    # the remote->center intertie limit is finite and modest
    assert iface.limit_mw < 1000.0


def test_mixed_synchronous_and_inverter_resources():
    world = build_default_world()
    techs = {g.technology for g in world.generators}
    assert GenTechnology.NUCLEAR in techs
    assert GenTechnology.WIND in techs
    assert GenTechnology.SOLAR_PV in techs
    # every generator has a dynamic model attached
    for g in world.generators:
        assert g.dynamic_model_id is not None


def test_has_storage_and_hydro():
    world = build_default_world()
    assert world.storage_units
    assert world.hydro_units
    # storage sizes power and energy independently
    s = world.storage_units[0]
    assert s.p_discharge_max_mw > 0 and s.energy_capacity_mwh > 0


def test_has_candidates_for_cem():
    world = build_default_world()
    cand_gens = [g for g in world.generators if g.is_candidate]
    cand_lines = [ln for ln in world.ac_lines if ln.is_candidate]
    assert cand_gens
    assert cand_lines


def test_weak_pocket_present():
    # a high-impedance feeder seeds the low-SCR EMT micro-example
    world = build_default_world()
    max_x = max(ln.x for ln in world.ac_lines)
    assert max_x >= 0.25


def test_dynamic_models_resolvable():
    world = build_default_world()
    ids = {m.id for m in world.dynamic_models}
    for g in world.generators:
        assert g.dynamic_model_id in ids


def test_weather_profiles_bound_to_vre():
    world, gt = build_default_world_with_weather()
    for g in world.generators:
        if g.is_vre:
            assert g.availability_profile_id in world.time_series_store
