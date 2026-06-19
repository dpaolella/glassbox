"""Build and persist the default seed world with weather (PRD Sections 8, 3.3).

Usage:
    python scripts/build_default_world.py [output_dir]

Emits ``world.json`` and ``timeseries.npz`` under the output directory
(default: ``data/default_world``).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from glassbox.world import build_default_world_with_weather, save_world


def main(out_dir: str = "data/default_world") -> None:
    t0 = time.time()
    print("Building default world + generating multi-year weather ...")
    world, gt = build_default_world_with_weather()
    paths = save_world(world, Path(out_dir))
    dt = time.time() - t0
    print(f"  buses={len(world.buses)} zones={len(world.zones)} "
          f"generators={len(world.generators)} series={len(world.time_series_store.series)}")
    print(f"  wrote {paths['world']}")
    print(f"  wrote {paths['timeseries']}")
    print(f"done in {dt:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/default_world")
