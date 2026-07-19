"""Glassbox schema — the spine (PRD Section 4).

Pydantic v2 models that double as the FastAPI request/response models. Field
metadata (facets, units, bases) drives the inspector and the attribute
projection operator for free.
"""

from .dynamic_models import (
    ConverterModel,
    DynamicKind,
    DynamicModel,
    LCLFilter,
    SynchronousMachineModel,
)
from .entities import (
    ACLine,
    Bus,
    BusType,
    CostCurve,
    CostCurveSegment,
    CandidateKind,
    DCLine,
    Disturbance,
    DisturbanceKind,
    ExpansionCandidate,
    FaultType,
    Fuel,
    GenTechnology,
    Generator,
    Hydro,
    HydroTechnology,
    Interface,
    InterfaceLimitSource,
    Load,
    Policy,
    PolicyKind,
    ReserveKind,
    ReserveProduct,
    ResourcePotential,
    ResourceStatus,
    Shunt,
    SupplyTranche,
    Storage,
    StorageTechnology,
    SystemConstraint,
    SystemConstraintKind,
    Transformer,
    Zone,
    ZonePartition,
)
from .facets import (
    FACET_DESCRIPTIONS,
    FACET_ENGINE,
    FACET_LABELS,
    Facet,
    all_facets_for,
    facet_field,
    field_facets,
    field_metadata,
    fields_in_facet,
)
from .results import (
    AdequacyResult,
    CEMResult,
    DispatchResult,
    DynamicsResult,
    EMTResult,
    ImpedanceScanResult,
    NetworkResult,
    PCMResult,
    PowerFlowResult,
    Provenance,
    ResultBase,
)
from .temporal import TemporalMap, TemporalMapKind
from .timeseries import TimeSeries, TimeSeriesKind, TimeSeriesStore
from .units import (
    PerUnitSystem,
    convert_inertia_to_system_base,
    convert_machine_to_system_base,
)
from .world import WeatherModelParams, WeatherSite, World

# Registry of inspectable entity models, keyed by a stable type name, for the
# generic introspection API (Section 9.2) and tests.
from .substation import (Substation, VoltageLevel, BusbarSection, ConnectivityNode,
                         Switch, SwitchKind, SubstationArrangement, EquipmentTerminal)

ENTITY_MODELS = {
    "Bus": Bus,
    "Zone": Zone,
    "ACLine": ACLine,
    "Transformer": Transformer,
    "DCLine": DCLine,
    "Shunt": Shunt,
    "Interface": Interface,
    "Generator": Generator,
    "Hydro": Hydro,
    "Storage": Storage,
    "Load": Load,
    "ExpansionCandidate": ExpansionCandidate,
    "ResourcePotential": ResourcePotential,
    "Fuel": Fuel,
    "CostCurve": CostCurve,
    "Policy": Policy,
    "ReserveProduct": ReserveProduct,
    "SystemConstraint": SystemConstraint,
    "Disturbance": Disturbance,
    "SynchronousMachineModel": SynchronousMachineModel,
    "ConverterModel": ConverterModel,
    "Substation": Substation,
    "VoltageLevel": VoltageLevel,
    "BusbarSection": BusbarSection,
    "ConnectivityNode": ConnectivityNode,
    "Switch": Switch,
    "EquipmentTerminal": EquipmentTerminal,
}

__all__ = [
    "Facet", "FACET_LABELS", "facet_field", "field_facets", "fields_in_facet",
    "field_metadata", "all_facets_for",
    "PerUnitSystem", "convert_machine_to_system_base", "convert_inertia_to_system_base",
    "TimeSeries", "TimeSeriesKind", "TimeSeriesStore",
    "TemporalMap", "TemporalMapKind",
    "Bus", "BusType", "Zone", "ZonePartition", "ACLine", "Transformer", "DCLine",
    "Shunt", "Interface", "InterfaceLimitSource", "Generator", "GenTechnology",
    "Hydro", "HydroTechnology", "Storage", "StorageTechnology", "Load",
    "ExpansionCandidate", "CandidateKind", "ResourceStatus",
    "ResourcePotential", "SupplyTranche",
    "Fuel", "CostCurve", "CostCurveSegment", "Policy", "PolicyKind",
    "ReserveProduct", "ReserveKind", "SystemConstraint", "SystemConstraintKind",
    "Disturbance", "DisturbanceKind", "FaultType",
    "DynamicModel", "DynamicKind", "SynchronousMachineModel", "ConverterModel", "LCLFilter",
    "Substation", "VoltageLevel", "BusbarSection", "ConnectivityNode",
    "Switch", "SwitchKind", "SubstationArrangement", "EquipmentTerminal",
    "World", "WeatherModelParams", "WeatherSite",
    "ResultBase", "Provenance", "CEMResult", "PCMResult", "DispatchResult", "NetworkResult",
    "AdequacyResult", "PowerFlowResult", "DynamicsResult", "EMTResult", "ImpedanceScanResult",
    "ENTITY_MODELS",
]
