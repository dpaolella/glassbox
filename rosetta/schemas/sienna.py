"""A lightweight Pydantic mirror of the SiennaGridDB data model (subset).

Purpose-built for the hub experiment: what matters here is the *shape* of
Sienna's schema — in particular its **closed technology taxonomy** (fixed
prime-mover and fuel enums, following PowerSystems.jl / EIA-923) — not a full
reimplementation. This mirror is derived from the G-PST data-schema-exercise
YAML and PowerSystems.jl type names; it is not affiliated with NREL and only
covers the components the test bench exercises.

The closed enums below ARE the experimental variable. A bridge translating
into this schema must produce a (prime_mover, fuel) pair for every generator;
whenever it cannot, that is a `manual_mapping_required` event in the coverage
manifest — the cost the "PyPSA as hub" one-pager attributes to opinionated
hubs, here made countable.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PrimeMover(str, Enum):
    """Subset of PowerSystems.jl PrimeMovers (EIA-923 codes)."""

    ST = "ST"    # steam turbine
    GT = "GT"    # gas turbine (simple cycle, part of CC)
    CC = "CC"    # combined cycle
    CT = "CT"    # combustion turbine
    IC = "IC"    # internal combustion
    HY = "HY"    # hydro
    WT = "WT"    # wind, onshore
    WS = "WS"    # wind, offshore
    PVe = "PVe"  # photovoltaic
    BA = "BA"    # battery
    PS = "PS"    # pumped storage
    OT = "OT"    # other


class ThermalFuel(str, Enum):
    """Subset of PowerSystems.jl ThermalFuels."""

    COAL = "COAL"
    NATURAL_GAS = "NATURAL_GAS"
    NUCLEAR = "NUCLEAR"
    GEOTHERMAL = "GEOTHERMAL"
    WASTE_BIOMASS = "WASTE_BIOMASS"
    OTHER = "OTHER"


class SiennaBus(BaseModel):
    name: str
    number: int
    base_voltage_kv: float = 0.0
    bus_type: str = "PQ"          # PQ | PV | REF
    area: Optional[str] = None
    load_zone: Optional[str] = None
    x: float = 0.0
    y: float = 0.0


class SiennaLine(BaseModel):
    name: str
    from_bus: str
    to_bus: str
    r_pu: float = 0.0             # system base
    x_pu: float = 0.0001
    b_pu: float = 0.0
    rating_mva: float = 0.0


class ThermalStandard(BaseModel):
    name: str
    bus: str
    prime_mover: PrimeMover       # CLOSED — the experimental variable
    fuel: ThermalFuel             # CLOSED
    active_power_limits_max_mw: float = 0.0
    active_power_limits_min_mw: float = 0.0
    variable_cost_per_mwh: float = 0.0
    available: bool = True


class RenewableDispatch(BaseModel):
    name: str
    bus: str
    prime_mover: PrimeMover       # WT / WS / PVe
    rating_mw: float = 0.0
    variable_cost_per_mwh: float = 0.0
    availability_series: Optional[str] = None   # out-of-line reference
    available: bool = True


class HydroDispatch(BaseModel):
    name: str
    bus: str
    prime_mover: PrimeMover = PrimeMover.HY
    active_power_limits_max_mw: float = 0.0
    storage_capacity_mwh: Optional[float] = None
    available: bool = True


class EnergyReservoirStorage(BaseModel):
    name: str
    bus: str
    prime_mover: PrimeMover = PrimeMover.BA
    input_active_power_limit_mw: float = 0.0
    output_active_power_limit_mw: float = 0.0
    storage_capacity_mwh: float = 0.0
    efficiency_in: float = 0.95
    efficiency_out: float = 0.95
    available: bool = True


class PowerLoad(BaseModel):
    name: str
    bus: str
    max_active_power_mw: float = 0.0
    demand_series: Optional[str] = None         # out-of-line reference
    available: bool = True


class StaticReserve(BaseModel):
    """Sienna has first-class reserve products (unlike PyPSA)."""

    name: str
    requirement_mw: float = 0.0
    time_frame_s: float = 600.0


class SiennaSystem(BaseModel):
    """The container. Time-series arrays travel out-of-line (see .series)."""

    name: str = "system"
    base_power_mva: float = 100.0
    buses: list[SiennaBus] = Field(default_factory=list)
    lines: list[SiennaLine] = Field(default_factory=list)
    thermal: list[ThermalStandard] = Field(default_factory=list)
    renewable: list[RenewableDispatch] = Field(default_factory=list)
    hydro: list[HydroDispatch] = Field(default_factory=list)
    storage: list[EnergyReservoirStorage] = Field(default_factory=list)
    loads: list[PowerLoad] = Field(default_factory=list)
    reserves: list[StaticReserve] = Field(default_factory=list)
    # id -> hourly array; kept as plain lists so the system dumps to JSON
    series: dict[str, list[float]] = Field(default_factory=dict)
