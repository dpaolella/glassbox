"""World serialization round-trip tests (PRD Section 3.3)."""

from __future__ import annotations

import numpy as np

from glassbox.world import build_default_world_with_weather, load_world, save_world


def test_world_roundtrip(tmp_path):
    world, _ = build_default_world_with_weather()
    save_world(world, tmp_path)
    loaded = load_world(tmp_path)

    assert len(loaded.buses) == len(world.buses)
    assert len(loaded.generators) == len(world.generators)
    assert len(loaded.dynamic_models) == len(world.dynamic_models)
    assert loaded.base_power_mva == world.base_power_mva
    assert loaded.reference_bus_id == world.reference_bus_id

    # time series arrays survive the npz round-trip
    sid = next(iter(world.time_series_store.arrays))
    np.testing.assert_allclose(
        loaded.time_series_store.get(sid), world.time_series_store.get(sid))


def test_dynamic_model_polymorphism_preserved(tmp_path):
    world, _ = build_default_world_with_weather()
    save_world(world, tmp_path)
    loaded = load_world(tmp_path)
    kinds = {type(m).__name__ for m in loaded.dynamic_models}
    assert "SynchronousMachineModel" in kinds
    assert "ConverterModel" in kinds
