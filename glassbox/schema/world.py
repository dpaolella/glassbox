"""The World container — the one stored, fine-grained world (PRD 4.4, 2.1).

Store exactly one fine-grained world at the positive-sequence nodal level. Every
modeling layer is a *view* of this one world produced by the projection
operators (Section 5). Do not store separate "zonal data" and "nodal data" or
"one-year" and "multi-year": store fine, derive coarse.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field

from .dynamic_models import ConverterModel, SynchronousMachineModel
from .entities import (
    ACLine,
    Bus,
    CostCurve,
    DCLine,
    Disturbance,
    Fuel,
    Generator,
    Hydro,
    Interface,
    Load,
    Policy,
    ReserveProduct,
    Shunt,
    Storage,
    SystemConstraint,
    Transformer,
    Zone,
)
from .facets import facet_field
from .temporal import TemporalMap
from .timeseries import TimeSeriesStore
from .units import PerUnitSystem


class WeatherModelParams(BaseModel):
    """Generative parameters for the synthetic weather generator (Section 7).

    Stored on the World so the ground truth that produced the time series is
    inspectable (Section 2.4). The generator in ``weather/`` reads this.
    """

    model_config = ConfigDict(extra="allow")

    seed: int = facet_field(facets=["core"], default=0)
    n_years: int = facet_field(facets=["core", "adq"], default=10)
    hours_per_year: int = facet_field(facets=["core"], default=8760)
    latitude_deg: float = facet_field(facets=["core"], default=40.0)

    # regime Markov chain
    regime_names: list[str] = facet_field(
        facets=["core"], default_factory=lambda: ["calm_high", "windy_frontal", "mixed"])
    regime_transition: list[list[float]] = facet_field(facets=["core"], default_factory=list,
                                                       description="row-stochastic transition matrix")

    # stochastic noise (OU/AR) and spatial correlation
    ou_theta: float = facet_field(facets=["core"], default=0.15,
                                  description="OU mean-reversion rate per hour")
    ou_sigma: float = facet_field(facets=["core"], default=1.0,
                                  description="OU noise scale")
    correlation_length_km: float = facet_field(facets=["core"], default=300.0)

    # inter-annual variability
    interannual_sigma: float = facet_field(facets=["core"], default=0.1)

    # conversion parameters
    wind_cut_in_ms: float = facet_field(facets=["core"], default=3.0)
    wind_rated_ms: float = facet_field(facets=["core"], default=12.0)
    wind_cut_out_ms: float = facet_field(facets=["core"], default=25.0)
    load_growth_per_year: float = facet_field(facets=["core"], default=0.01)
    load_base_mw: float = facet_field(facets=["core"], default=1.0,
                                      description="per-load scale applied to shape")
    temp_heating_coef: float = facet_field(facets=["core"], default=0.02)
    temp_cooling_coef: float = facet_field(facets=["core"], default=0.03)


class WeatherSite(BaseModel):
    """A geographic point that weather is generated for, bound to entities."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    kind: str = facet_field(facets=["core"], default="wind", description="wind|solar|load")
    x: float = facet_field(facets=["core"], default=0.0)
    y: float = facet_field(facets=["core"], default=0.0)
    scale: float = facet_field(facets=["core"], default=1.0,
                               description="amplitude scale (e.g. peak MW for a load site)")


class World(BaseModel):
    """Top-level container (PRD 4.4)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = facet_field(facets=["core"], default="default")
    name: str = facet_field(facets=["core"], default="")
    description: str = facet_field(facets=["core"], default="")

    base_power_mva: float = facet_field(facets=["core", "pf", "dyn"], unit="MVA", default=100.0,
                                        description="system per-unit base")
    base_frequency_hz: float = facet_field(facets=["core", "dyn", "emt"], unit="Hz", default=60.0)
    reference_bus_id: str = facet_field(facets=["pf", "dyn"], default="",
                                        description="slack / angle reference")

    # topology
    buses: list[Bus] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    ac_lines: list[ACLine] = Field(default_factory=list)
    transformers: list[Transformer] = Field(default_factory=list)
    dc_lines: list[DCLine] = Field(default_factory=list)
    shunts: list[Shunt] = Field(default_factory=list)
    interfaces: list[Interface] = Field(default_factory=list)

    # resources
    generators: list[Generator] = Field(default_factory=list)
    hydro_units: list[Hydro] = Field(default_factory=list)
    storage_units: list[Storage] = Field(default_factory=list)
    loads: list[Load] = Field(default_factory=list)

    # dynamic models attached by id from generators/storage/dc_lines/shunts
    dynamic_models: list[SynchronousMachineModel | ConverterModel] = Field(default_factory=list)

    # cost / policy / constraints
    fuels: list[Fuel] = Field(default_factory=list)
    cost_curves: list[CostCurve] = Field(default_factory=list)
    policies: list[Policy] = Field(default_factory=list)
    reserve_products: list[ReserveProduct] = Field(default_factory=list)
    system_constraints: list[SystemConstraint] = Field(default_factory=list)
    disturbances: list[Disturbance] = Field(default_factory=list)

    # temporal + weather
    temporal_maps: list[TemporalMap] = Field(default_factory=list)
    weather_model: Optional[WeatherModelParams] = None
    weather_sites: list[WeatherSite] = Field(default_factory=list)

    # multi-year series (arrays excluded from JSON; persisted separately)
    time_series_store: TimeSeriesStore = Field(default_factory=TimeSeriesStore)

    # --- lookup helpers ---------------------------------------------------

    def per_unit_system(self) -> PerUnitSystem:
        return PerUnitSystem(self.base_power_mva, self.base_frequency_hz)

    def _index(self, collection: list, key: str = "id") -> dict:
        return {getattr(o, key): o for o in collection}

    def bus(self, bus_id: str) -> Bus:
        return self._index(self.buses)[bus_id]

    def zone(self, zone_id: str) -> Zone:
        return self._index(self.zones)[zone_id]

    def generator(self, gen_id: str) -> Generator:
        return self._index(self.generators)[gen_id]

    def dynamic_model(self, model_id: str):
        return self._index(self.dynamic_models)[model_id]

    def temporal_map(self, map_id: str) -> TemporalMap:
        return self._index(self.temporal_maps)[map_id]

    @property
    def branches(self) -> list:
        """All electrical branches (AC lines + transformers) for topology."""
        return list(self.ac_lines) + list(self.transformers)

    ENTITY_COLLECTIONS: ClassVar[tuple[str, ...]] = (
        "buses", "zones", "ac_lines", "transformers", "dc_lines", "shunts",
        "interfaces", "generators", "hydro_units", "storage_units", "loads",
        "fuels", "cost_curves", "policies", "reserve_products",
        "system_constraints", "disturbances",
    )
