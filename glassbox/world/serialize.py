"""World persistence (PRD Section 3.3).

Serialize the static schema to JSON and the multi-year time series to a compact
binary (npz). A scenario is a small JSON document referencing a world; results
are serialized alongside their scenario with provenance.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..schema import World
from ..schema.timeseries import TimeSeries, TimeSeriesStore


def save_world(world: World, directory: str | Path) -> dict[str, str]:
    """Write ``world.json`` (schema) and ``timeseries.npz`` (arrays)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    json_path = directory / "world.json"
    # exclude the array store from the JSON dump (arrays go to npz)
    data = world.model_dump(mode="json", exclude={"time_series_store"})
    # keep the series metadata (without arrays) in the JSON for completeness
    data["time_series_store"] = {
        "series": {k: v.model_dump(mode="json")
                   for k, v in world.time_series_store.series.items()},
    }
    json_path.write_text(json.dumps(data, indent=2))

    npz_path = directory / "timeseries.npz"
    arrays = world.time_series_store.arrays
    if arrays:
        np.savez_compressed(npz_path, **arrays)
    return {"world": str(json_path), "timeseries": str(npz_path)}


def load_world(directory: str | Path) -> World:
    directory = Path(directory)
    data = json.loads((directory / "world.json").read_text())

    series_meta = data.pop("time_series_store", {}).get("series", {})
    world = World.model_validate(data)

    store = TimeSeriesStore()
    npz_path = directory / "timeseries.npz"
    arrays = {}
    if npz_path.exists():
        with np.load(npz_path) as npz:
            arrays = {k: npz[k] for k in npz.files}
    for sid, meta in series_meta.items():
        ts = TimeSeries.model_validate(meta)
        store.series[sid] = ts
        if sid in arrays:
            store.arrays[sid] = arrays[sid]
    world.time_series_store = store
    return world
