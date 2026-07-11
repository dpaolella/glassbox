"""Entity models (PRD Section 4.5).

Every load-bearing field carries its facet tags, unit, and per-unit base via
``facet_field``. Field selection is intentionally faithful to the PRD tables;
the implementer is invited (Section 4.5) to add obvious fields following the same
pattern, which a few entities do.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from .facets import facet_field


# --- enums --------------------------------------------------------------------


class BusType(str, Enum):
    PQ = "PQ"
    PV = "PV"
    SLACK = "slack"


class ZonePartition(str, Enum):
    BALANCING = "balancing"
    RESERVE = "reserve"
    PRICE = "price"


class EMTLineModel(str, Enum):
    PI = "pi"
    DISTRIBUTED = "distributed"


class GenTechnology(str, Enum):
    COAL = "coal"
    CCGT = "ccgt"
    OCGT = "ocgt"
    NUCLEAR = "nuclear"
    WIND = "wind"
    SOLAR_PV = "solar_pv"
    GEOTHERMAL = "geothermal"
    BIOMASS = "biomass"


class HydroTechnology(str, Enum):
    RESERVOIR = "reservoir"
    RUN_OF_RIVER = "run_of_river"
    PUMPED = "pumped"


class StorageTechnology(str, Enum):
    BATTERY = "battery"
    PUMPED_HYDRO = "pumped_hydro"
    LDES = "ldes"


class ResourceStatus(str, Enum):
    """Lifecycle of a physical asset. Build *options* are not a status — they are
    a separate ExpansionCandidate entity (Section: investment vs operations)."""

    EXISTING = "existing"
    RETIRED = "retired"


class CandidateKind(str, Enum):
    GENERATOR = "generator"
    STORAGE = "storage"
    LINE = "line"


class FuelEmissionsScope(str, Enum):
    pass


# --- topology -----------------------------------------------------------------


class Bus(BaseModel):
    """PRD 4.5.1."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    base_kv: float = facet_field(facets=["core", "pf"], unit="kV", default=230.0,
                                 description="nominal voltage")
    zone_id: str = facet_field(facets=["core", "inv", "ops", "adq"], default="",
                               description="spatial aggregation key")
    x: float = facet_field(facets=["core"], default=0.0, description="map x coordinate")
    y: float = facet_field(facets=["core"], default=0.0, description="map y coordinate")
    v_min_pu: float = facet_field(facets=["pf"], unit="pu", default=0.95)
    v_max_pu: float = facet_field(facets=["pf"], unit="pu", default=1.05)
    bus_type: BusType = facet_field(facets=["pf"], default=BusType.PQ,
                                    description="partly derived from attached devices")


class Zone(BaseModel):
    """PRD 4.5.2 (Area)."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    member_bus_ids: list[str] = facet_field(facets=["core"], default_factory=list,
                                            description="the aggregation map")
    partition: ZonePartition = facet_field(facets=["core"], default=ZonePartition.BALANCING)


class ACLine(BaseModel):
    """PRD 4.5.3. Pi model; b is total line charging."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    from_bus_id: str = facet_field(facets=["core", "pf", "dyn"])
    to_bus_id: str = facet_field(facets=["core", "pf", "dyn"])
    in_service: bool = facet_field(facets=["core"], default=True)
    r: float = facet_field(facets=["pf", "dyn", "emt"], unit="pu", default=0.01,
                           description="series resistance (system pu)")
    x: float = facet_field(facets=["pf", "dyn", "emt"], unit="pu", default=0.1,
                           description="series reactance (system pu)")
    b: float = facet_field(facets=["pf", "dyn", "emt"], unit="pu", default=0.0,
                           description="total line charging susceptance (system pu)")
    length_km: float = facet_field(facets=["core", "emt"], unit="km", default=100.0)
    rating_normal_mva: float = facet_field(facets=["ops", "pf", "inv"], unit="MVA", default=500.0)
    rating_emergency_mva: float = facet_field(facets=["pf"], unit="MVA", default=600.0,
                                              description="post-contingency limit")
    rating_lt_mva: float = facet_field(facets=["pf"], unit="MVA", default=550.0,
                                       description="long-term emergency")
    r_per_km: Optional[float] = facet_field(facets=["emt"], unit="ohm/km", default=None)
    l_per_km: Optional[float] = facet_field(facets=["emt"], unit="H/km", default=None)
    c_per_km: Optional[float] = facet_field(facets=["emt"], unit="F/km", default=None)
    emt_line_model: EMTLineModel = facet_field(facets=["emt"], default=EMTLineModel.PI)


class Transformer(BaseModel):
    """PRD 4.5.4. Reactances on system base."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    from_bus_id: str = facet_field(facets=["core", "pf", "dyn"])
    to_bus_id: str = facet_field(facets=["core", "pf", "dyn"])
    r: float = facet_field(facets=["pf", "dyn"], unit="pu", default=0.0)
    x: float = facet_field(facets=["pf", "dyn"], unit="pu", default=0.1)
    rating_mva: float = facet_field(facets=["ops", "pf"], unit="MVA", default=500.0)
    tap_ratio: float = facet_field(facets=["pf"], unit="pu", default=1.0)
    phase_shift_deg: float = facet_field(facets=["pf", "ops"], unit="deg", default=0.0,
                                         description="phase-shifting control")
    oltc_enabled: bool = facet_field(facets=["pf", "dyn"], default=False,
                                     description="on-load tap changer")
    tap_min: float = facet_field(facets=["pf"], unit="pu", default=0.9)
    tap_max: float = facet_field(facets=["pf"], unit="pu", default=1.1)
    tap_step: float = facet_field(facets=["pf"], unit="pu", default=0.0125)


class DCLine(BaseModel):
    """PRD 4.5.5. Controllable transfer (HVDC)."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    from_bus_id: str = facet_field(facets=["core", "pf"])
    to_bus_id: str = facet_field(facets=["core", "pf"])
    p_max_mw: float = facet_field(facets=["ops", "pf", "inv"], unit="MW", default=500.0)
    loss_fraction: float = facet_field(facets=["ops", "pf"], default=0.02)
    dynamic_model_id: Optional[str] = facet_field(facets=["dyn", "emt"], default=None,
                                                  description="converter model (IBR)")


class Shunt(BaseModel):
    """PRD 4.5.6."""

    id: str = facet_field(facets=["core"])
    bus_id: str = facet_field(facets=["core", "pf"])
    g: float = facet_field(facets=["pf"], unit="pu", default=0.0, description="conductance")
    b: float = facet_field(facets=["pf"], unit="pu", default=0.0, description="susceptance")
    controllable: bool = facet_field(facets=["pf", "dyn"], default=False,
                                     description="true for SVC/STATCOM")
    q_min_mvar: Optional[float] = facet_field(facets=["pf", "dyn"], unit="MVAr", default=None)
    q_max_mvar: Optional[float] = facet_field(facets=["pf", "dyn"], unit="MVAr", default=None)
    dynamic_model_id: Optional[str] = facet_field(facets=["dyn", "emt"], default=None,
                                                  description="converter model if STATCOM")


class InterfaceLimitSource(str, Enum):
    THERMAL = "thermal"
    STABILITY = "stability"
    MANUAL = "manual"


class Interface(BaseModel):
    """PRD 4.5.7 (Flowgate). Stability limits flow down from dynamics (6.7)."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    member_line_ids: list[str] = facet_field(facets=["ops", "pf"], default_factory=list,
                                             description="the monitored cut")
    direction_weights: list[float] = facet_field(facets=["ops", "pf"], default_factory=list,
                                                 description="flow-direction signs")
    limit_mw: float = facet_field(facets=["ops"], unit="MW", default=1e9,
                                  description="aggregate transfer limit")
    limit_source: InterfaceLimitSource = facet_field(
        facets=["ops", "dyn"], default=InterfaceLimitSource.THERMAL,
        description="stability limits flow down from the dynamics layer")


# --- generators and loads -----------------------------------------------------


class Generator(BaseModel):
    """PRD 4.5.8 — the most faceted entity, grouped by facet."""

    # identity (core)
    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    bus_id: str = facet_field(facets=["core"])
    technology: GenTechnology = facet_field(facets=["core"], default=GenTechnology.CCGT)
    fuel_id: Optional[str] = facet_field(facets=["core"], default=None)
    prime_mover: str = facet_field(facets=["core"], default="")
    in_service: bool = facet_field(facets=["core"], default=True)
    retirement_year: Optional[int] = facet_field(facets=["core"], default=None)
    status: ResourceStatus = facet_field(
        facets=["core", "inv"], default=ResourceStatus.EXISTING,
        description="lifecycle of this physical asset (existing/retired); "
                    "build options live in ExpansionCandidate, not here")

    # investment / fixed cost of the existing asset (inv)
    fom_per_mw_yr: float = facet_field(facets=["inv"], unit="currency/MW/yr", default=0.0)
    lifetime_yr: int = facet_field(facets=["inv"], unit="yr", default=30)

    # operations (ops)
    p_max_mw: float = facet_field(facets=["ops"], unit="MW", default=100.0,
                                  description="installed nameplate capacity")
    p_min_pu: float = facet_field(facets=["ops"], unit="pu", default=0.0,
                                  description="min stable level")
    heat_rate_mmbtu_per_mwh: Optional[float] = facet_field(facets=["ops"], unit="MMBtu/MWh",
                                                           default=None)
    cost_curve_id: Optional[str] = facet_field(facets=["ops"], default=None)
    vom_per_mwh: float = facet_field(facets=["ops"], unit="currency/MWh", default=0.0)
    ramp_up_mw_per_min: Optional[float] = facet_field(facets=["ops"], unit="MW/min", default=None)
    ramp_down_mw_per_min: Optional[float] = facet_field(facets=["ops"], unit="MW/min", default=None)
    min_up_time_h: float = facet_field(facets=["ops"], unit="h", default=0.0)
    min_down_time_h: float = facet_field(facets=["ops"], unit="h", default=0.0)
    start_cost: float = facet_field(facets=["ops"], unit="currency", default=0.0)
    no_load_cost: float = facet_field(facets=["ops"], unit="currency/h", default=0.0)
    reserve_eligible: list[str] = facet_field(facets=["ops"], default_factory=list)
    availability_profile_id: Optional[str] = facet_field(
        facets=["ops"], default=None,
        description="VRE only — input profile; realized capacity factor is an output")

    # adequacy (adq)
    mttf_h: Optional[float] = facet_field(facets=["adq"], unit="h", default=None,
                                          description="mean time to failure")
    mttr_h: Optional[float] = facet_field(facets=["adq"], unit="h", default=None,
                                          description="mean time to repair")
    maintenance_weeks: float = facet_field(facets=["adq"], unit="weeks", default=0.0)

    # power flow (pf)
    p_setpoint_mw: Optional[float] = facet_field(facets=["pf"], unit="MW", default=None,
                                                 description="from dispatch")
    q_min_mvar: Optional[float] = facet_field(facets=["pf"], unit="MVAr", default=None)
    q_max_mvar: Optional[float] = facet_field(facets=["pf"], unit="MVAr", default=None)
    v_setpoint_pu: Optional[float] = facet_field(facets=["pf"], unit="pu", default=None)
    mva_base: float = facet_field(facets=["pf", "dyn"], unit="MVA", default=100.0)

    # dynamics + emt
    dynamic_model_id: Optional[str] = facet_field(facets=["dyn", "emt"], default=None)

    @property
    def is_vre(self) -> bool:
        return self.technology in (GenTechnology.WIND, GenTechnology.SOLAR_PV)


class Hydro(BaseModel):
    """PRD 4.5.9."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    bus_id: str = facet_field(facets=["core"])
    in_service: bool = facet_field(facets=["core"], default=True)
    technology: HydroTechnology = facet_field(facets=["core"], default=HydroTechnology.RESERVOIR)
    p_max_mw: float = facet_field(facets=["ops", "inv"], unit="MW", default=100.0)
    p_min_pu: float = facet_field(facets=["ops", "inv"], unit="pu", default=0.0)
    reservoir_energy_mwh: float = facet_field(facets=["ops", "inv"], unit="MWh", default=0.0,
                                              description="usable storage")
    inflow_profile_id: Optional[str] = facet_field(facets=["ops", "adq"], default=None)
    cascade_downstream_id: Optional[str] = facet_field(facets=["ops"], default=None)
    mva_base: float = facet_field(facets=["pf", "dyn"], unit="MVA", default=100.0)
    dynamic_model_id: Optional[str] = facet_field(facets=["dyn", "emt"], default=None,
                                                  description="synchronous machine")


class Storage(BaseModel):
    """PRD 4.5.10. Power and energy are sized independently (Section 1.3)."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    bus_id: str = facet_field(facets=["core"])
    in_service: bool = facet_field(facets=["core"], default=True)
    technology: StorageTechnology = facet_field(facets=["core"], default=StorageTechnology.BATTERY)
    p_charge_max_mw: float = facet_field(facets=["ops", "inv", "pf"], unit="MW", default=50.0,
                                         description="independent of energy")
    p_discharge_max_mw: float = facet_field(facets=["ops", "inv", "pf"], unit="MW", default=50.0,
                                            description="independent of energy")
    energy_capacity_mwh: float = facet_field(facets=["ops", "inv"], unit="MWh", default=200.0,
                                             description="independent of power")
    efficiency_charge: float = facet_field(facets=["ops"], default=0.95)
    efficiency_discharge: float = facet_field(facets=["ops"], default=0.95)
    self_discharge_per_h: float = facet_field(facets=["ops"], unit="/h", default=0.0)
    soc_min_pu: float = facet_field(facets=["ops"], unit="pu", default=0.0)
    soc_max_pu: float = facet_field(facets=["ops"], unit="pu", default=1.0)
    vom_per_mwh: float = facet_field(facets=["ops"], unit="currency/MWh", default=0.0)
    fom_per_mw_yr: float = facet_field(facets=["inv"], unit="currency/MW/yr", default=0.0)
    lifetime_yr: int = facet_field(facets=["inv"], unit="yr", default=15)
    status: ResourceStatus = facet_field(facets=["core", "inv"],
                                         default=ResourceStatus.EXISTING)
    mttf_h: Optional[float] = facet_field(facets=["adq"], unit="h", default=None)
    mttr_h: Optional[float] = facet_field(facets=["adq"], unit="h", default=None)
    mva_base: float = facet_field(facets=["pf", "dyn"], unit="MVA", default=100.0)
    dynamic_model_id: Optional[str] = facet_field(facets=["dyn", "emt"], default=None,
                                                  description="converter or synchronous (pumped)")


class Load(BaseModel):
    """PRD 4.5.11."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    bus_id: str = facet_field(facets=["core"])
    zone_id: str = facet_field(facets=["core"], default="")
    demand_profile_id: Optional[str] = facet_field(facets=["ops", "adq", "inv"], default=None,
                                                   description="multi-year, weather-driven")
    power_factor: float = facet_field(facets=["pf"], default=0.98, description="for Q")
    zip_z: float = facet_field(facets=["pf", "dyn"], default=0.0)
    zip_i: float = facet_field(facets=["pf", "dyn"], default=0.0)
    zip_p: float = facet_field(facets=["pf", "dyn"], default=1.0)
    motor_fraction: float = facet_field(facets=["dyn"], default=0.0,
                                        description="induction-motor share; voltage stability")
    voll_per_mwh: float = facet_field(facets=["ops", "adq"], unit="currency/MWh", default=10000.0,
                                      description="value of lost load")
    dr_sheddable_mw: float = facet_field(facets=["ops"], unit="MW", default=0.0)
    dr_shiftable_mw: float = facet_field(facets=["ops"], unit="MW", default=0.0)


class ExpansionCandidate(BaseModel):
    """A buildable investment option — *not* a physical asset (Sienna-style:
    the Investments domain, separate from Operations).

    Only the capacity-expansion (`inv`) layer consumes these. They carry the
    siting, build limits / resource potential, economics, and an operating
    template describing how the resource would run once built. When CEM chooses
    to build one, it materializes a Generator / Storage / line of that size.
    """

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    kind: CandidateKind = facet_field(facets=["core", "inv"],
                                      default=CandidateKind.GENERATOR)
    technology: str = facet_field(facets=["core", "inv"], default="",
                                  description="ccgt, wind, solar_pv, battery, line, …")

    # siting (a bus for generators/storage; a pair for transmission)
    bus_id: Optional[str] = facet_field(facets=["core"], default=None)
    zone_id: Optional[str] = facet_field(facets=["core"], default=None)
    from_bus_id: Optional[str] = facet_field(facets=["core"], default=None)
    to_bus_id: Optional[str] = facet_field(facets=["core"], default=None)

    # build limits / resource potential
    build_min_mw: float = facet_field(facets=["inv"], unit="MW", default=0.0)
    build_max_mw: Optional[float] = facet_field(
        facets=["inv"], unit="MW", default=None,
        description="maximum buildable potential at this site (resource ceiling)")

    # economics (annualized inside the engine via a capital recovery factor)
    capex_per_mw: Optional[float] = facet_field(facets=["inv"], unit="currency/MW", default=None)
    capex_per_mwh: Optional[float] = facet_field(facets=["inv"], unit="currency/MWh",
                                                 default=None, description="storage energy capex")
    fom_per_mw_yr: float = facet_field(facets=["inv"], unit="currency/MW/yr", default=0.0)
    lifetime_yr: int = facet_field(facets=["inv"], unit="yr", default=30)

    # operating template — how it would run once built
    fuel_id: Optional[str] = facet_field(facets=["inv"], default=None)
    heat_rate_mmbtu_per_mwh: Optional[float] = facet_field(facets=["inv"], unit="MMBtu/MWh",
                                                           default=None)
    vom_per_mwh: float = facet_field(facets=["inv"], unit="currency/MWh", default=0.0)
    p_min_pu: float = facet_field(facets=["inv"], unit="pu", default=0.0)
    availability_profile_id: Optional[str] = facet_field(
        facets=["inv"], default=None, description="VRE candidate availability profile")
    resource_class: Optional[str] = facet_field(facets=["inv"], default=None)

    # storage template
    duration_h: Optional[float] = facet_field(facets=["inv"], unit="h", default=None,
                                              description="energy/power ratio for storage")
    efficiency_charge: float = facet_field(facets=["inv"], default=0.95)
    efficiency_discharge: float = facet_field(facets=["inv"], default=0.95)

    # transmission template
    reactance_pu: Optional[float] = facet_field(facets=["inv"], unit="pu", default=None)

    # rough metrics surfaced on the Resource Potential map (optional/derivable)
    expected_capacity_factor: Optional[float] = facet_field(facets=["inv"], default=None)
    lcoe_per_mwh: Optional[float] = facet_field(facets=["inv"], unit="currency/MWh", default=None)


class SupplyTranche(BaseModel):
    """One step of a zonal resource supply curve.

    The best (cheapest, highest-yield) sites in a zone are exhausted first, so a
    technology's buildable potential is a *rising* curve of $/MW, not a single
    price. Each tranche is an incremental block of capacity at its own cost.
    """

    build_max_mw: float = facet_field(facets=["inv"], unit="MW", default=0.0,
                                      description="incremental buildable capacity in this step")
    capex_per_mw: float = facet_field(facets=["inv"], unit="currency/MW", default=0.0)
    capex_per_mwh: Optional[float] = facet_field(facets=["inv"], unit="currency/MWh",
                                                 default=None, description="storage energy capex")
    fom_per_mw_yr: Optional[float] = facet_field(
        facets=["inv"], unit="currency/MW/yr", default=None,
        description="overrides the parent FOM for this tranche if set")
    expected_capacity_factor: Optional[float] = facet_field(
        facets=["inv"], default=None, description="better sites (early tranches) yield more")
    availability_profile_id: Optional[str] = facet_field(
        facets=["inv"], default=None, description="overrides the parent profile if set")
    bus_id: Optional[str] = facet_field(
        facets=["inv"], default=None,
        description="interconnection bus for this step (defaults to the curve's hub)")
    lcoe_per_mwh: Optional[float] = facet_field(facets=["inv"], unit="currency/MWh", default=None)


class ResourcePotential(BaseModel):
    """A *zonal* supply curve of buildable resource (early-screening granularity).

    Where an ``ExpansionCandidate`` is a specific plant at a specific bus, a
    ResourcePotential is the aggregate buildable potential of a technology across
    a whole zone, expressed as a stepped supply curve (``tranches``). The two
    coexist: zonal supply curves answer "how much wind *could* this region host
    and at what rising cost", while nodal candidates answer "should we build
    *this* plant *here*". CEM builds tranches cheapest-first up to the potential,
    siting the build at the zone's interconnection hub.
    """

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    kind: CandidateKind = facet_field(facets=["core", "inv"],
                                      default=CandidateKind.GENERATOR)
    technology: str = facet_field(facets=["core", "inv"], default="",
                                  description="wind, solar_pv, battery, …")
    zone_id: str = facet_field(facets=["core", "inv"], default="",
                               description="the zone whose resource potential this describes")
    bus_id: Optional[str] = facet_field(
        facets=["core"], default=None,
        description="representative interconnection bus; defaults to the zone hub")
    resource_class: Optional[str] = facet_field(facets=["inv"], default=None)

    # operating template shared by all tranches (how the resource runs once built)
    availability_profile_id: Optional[str] = facet_field(facets=["inv"], default=None)
    fuel_id: Optional[str] = facet_field(facets=["inv"], default=None)
    heat_rate_mmbtu_per_mwh: Optional[float] = facet_field(facets=["inv"], unit="MMBtu/MWh",
                                                           default=None)
    vom_per_mwh: float = facet_field(facets=["inv"], unit="currency/MWh", default=0.0)
    p_min_pu: float = facet_field(facets=["inv"], unit="pu", default=0.0)
    fom_per_mw_yr: float = facet_field(facets=["inv"], unit="currency/MW/yr", default=0.0)
    lifetime_yr: int = facet_field(facets=["inv"], unit="yr", default=25)

    # storage template
    duration_h: Optional[float] = facet_field(facets=["inv"], unit="h", default=None)
    efficiency_charge: float = facet_field(facets=["inv"], default=0.95)
    efficiency_discharge: float = facet_field(facets=["inv"], default=0.95)
    capex_per_mwh: Optional[float] = facet_field(facets=["inv"], unit="currency/MWh", default=None)

    tranches: list[SupplyTranche] = facet_field(facets=["inv"], default_factory=list)


# --- supporting objects -------------------------------------------------------


class Fuel(BaseModel):
    """PRD 4.5.13. price_per_mmbtu scalar or ref TimeSeries by year."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    price_per_mmbtu: float = facet_field(facets=["ops", "inv"], unit="currency/MMBtu", default=0.0)
    price_profile_id: Optional[str] = facet_field(facets=["inv"], default=None,
                                                  description="price by year for horizon")
    emissions_tco2_per_mmbtu: float = facet_field(facets=["ops", "inv"], unit="tCO2/MMBtu",
                                                  default=0.0)


class CostCurveSegment(BaseModel):
    breakpoint_mw: float = facet_field(facets=["ops"], unit="MW", default=0.0)
    marginal_cost_per_mwh: float = facet_field(facets=["ops"], unit="currency/MWh", default=0.0)


class CostCurve(BaseModel):
    """PRD 4.5.14. Piecewise-linear convex by default."""

    id: str = facet_field(facets=["core"])
    segments: list[CostCurveSegment] = facet_field(facets=["ops"], default_factory=list)
    startup_cost: float = facet_field(facets=["ops"], unit="currency", default=0.0)
    no_load_cost: float = facet_field(facets=["ops"], unit="currency/h", default=0.0)


class PolicyKind(str, Enum):
    EMISSIONS_CAP = "emissions_cap"
    CARBON_PRICE = "carbon_price"
    RPS = "rps"
    CES = "ces"
    PLANNING_RESERVE_MARGIN = "planning_reserve_margin"


class Policy(BaseModel):
    """PRD 4.5.15."""

    id: str = facet_field(facets=["core"])
    kind: PolicyKind = facet_field(facets=["inv", "ops"], default=PolicyKind.CARBON_PRICE)
    value: float = facet_field(facets=["inv", "ops"], default=0.0)
    scope_zone_ids: list[str] = facet_field(facets=["inv", "ops"], default_factory=list)
    applies_to_technologies: list[str] = facet_field(facets=["inv", "ops"], default_factory=list)


class ReserveKind(str, Enum):
    SPINNING = "spinning"
    NON_SPINNING = "non_spinning"
    REGULATION = "regulation"
    FAST_FREQUENCY_RESPONSE = "fast_frequency_response"


class ReserveProduct(BaseModel):
    """PRD 4.5.16. FFR requirement informed by dynamics layer (6.7)."""

    id: str = facet_field(facets=["core"])
    kind: ReserveKind = facet_field(facets=["ops"], default=ReserveKind.SPINNING)
    requirement_rule: dict[str, float] = facet_field(facets=["ops"], default_factory=dict,
                                                     description="e.g. pct_load, pct_vre, fixed_mw")
    zone_scope: list[str] = facet_field(facets=["ops"], default_factory=list)


class SystemConstraintKind(str, Enum):
    MIN_INERTIA = "min_inertia"
    MIN_SYNCHRONOUS_UNITS = "min_synchronous_units"
    ROCOF_LIMIT = "rocof_limit"
    MIN_SYSTEM_STRENGTH = "min_system_strength"


class SystemConstraint(BaseModel):
    """PRD 4.5.17. Carries a dynamics-derived requirement up into planning/ops."""

    id: str = facet_field(facets=["core"])
    kind: SystemConstraintKind = facet_field(facets=["inv", "ops", "dyn"],
                                             default=SystemConstraintKind.MIN_INERTIA)
    value: float = facet_field(facets=["inv", "ops", "dyn"], default=0.0)
    scope: list[str] = facet_field(facets=["inv", "ops", "dyn"], default_factory=list)


class DisturbanceKind(str, Enum):
    ELEMENT_OUTAGE = "element_outage"
    BUS_FAULT = "bus_fault"
    LINE_FAULT = "line_fault"


class FaultType(str, Enum):
    THREE_PHASE = "three_phase"
    SINGLE_LINE_GROUND = "single_line_ground"
    LINE_LINE = "line_line"
    DOUBLE_LINE_GROUND = "double_line_ground"


class Disturbance(BaseModel):
    """PRD 4.5.18 — one object serving three layers at three fidelities."""

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    affected_element_ids: list[str] = facet_field(facets=["pf", "dyn", "emt"], default_factory=list)
    kind: DisturbanceKind = facet_field(facets=["pf", "dyn", "emt"],
                                        default=DisturbanceKind.ELEMENT_OUTAGE)
    # dynamics + emt detail
    fault_type: Optional[FaultType] = facet_field(facets=["dyn", "emt"], default=None)
    fault_impedance_ohm: Optional[float] = facet_field(facets=["dyn", "emt"], unit="ohm",
                                                       default=None)
    apply_time_s: Optional[float] = facet_field(facets=["dyn", "emt"], unit="s", default=None)
    clear_time_s: Optional[float] = facet_field(
        facets=["dyn", "emt"], unit="s", default=None,
        description="set by protection; drives stability margin")
