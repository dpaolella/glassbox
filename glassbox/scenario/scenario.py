"""The Scenario object — the experiment unit (PRD Section 10).

A Scenario selects a world, an operator triple (spatial + temporal + the layer's
attribute facet), weather years, a target engine, and field-level overrides.
Running it applies the operators and overrides, invokes the engine, and stores
the result with provenance. Canonical demonstrations are pairs of scenarios that
differ in exactly one operator or override.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SpatialOperator(str, Enum):
    IDENTITY = "identity"     # nodal
    AGGREGATE = "aggregate"   # zonal


class Layer(str, Enum):
    CEM = "cem"
    PCM = "pcm"
    RA = "ra"
    PF = "pf"


class Override(BaseModel):
    """A field-level edit applied to the world for this scenario.

    kinds:
      * set_policy   — set a policy value by its kind (carbon_price, rps, ...)
      * set_field    — set any field on any entity by collection + id + field
      * retire       — set in_service=false / drop a unit by id
      * scale_field  — multiply a numeric field by a factor
    """

    kind: str
    policy_kind: str | None = None
    collection: str | None = None
    id: str | None = None
    field: str | None = None
    value: Any = None
    factor: float | None = None
    note: str = ""


class Scenario(BaseModel):
    id: str
    name: str = ""
    world_id: str = "default"
    spatial_operator: SpatialOperator = SpatialOperator.IDENTITY
    temporal_map_id: str = "representative_days"  # or "full_chronology"
    weather_years: list[int] = Field(default_factory=lambda: [0])
    layer: Layer = Layer.CEM
    overrides: list[Override] = Field(default_factory=list)

    # engine knobs (kept small and inspectable)
    n_rep_days: int = 8          # CEM representative-day count
    horizon_hours: int = 168     # PCM chronological window length
    horizon_start: int = 0       # PCM window start (hour within first weather year)

    # resource-adequacy knobs (Section 6.4)
    ra_n_draws: int = 40         # Monte Carlo draws
    ra_seed: int = 0
    ra_elcc_resource_ids: list[str] = Field(default_factory=list)

    # power-flow knobs (Section 6.5)
    pf_hour: int | None = None              # snapshot hour (default: annual peak)
    pf_dispatch_mode: str = "nodal"          # operating point: nodal | zonal
    pf_run_contingencies: bool = True
