"""Reference-system construction and serialization (PRD Sections 8, 3.3)."""

from .reference import (
    ReferenceSystemBuilder,
    ReferenceSystemParams,
    build_default_world,
)
from .serialize import load_world, save_world


def build_default_world_with_weather():
    """Build the default world and populate its multi-year weather series."""
    from ..weather import WeatherGenerator

    world = build_default_world()
    gen = WeatherGenerator(world.weather_model, world.weather_sites)
    gt = gen.generate(world.time_series_store)
    return world, gt


__all__ = [
    "ReferenceSystemBuilder", "ReferenceSystemParams", "build_default_world",
    "build_default_world_with_weather", "save_world", "load_world",
]
