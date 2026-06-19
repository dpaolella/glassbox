"""Modeling engines, one per rung of the abstraction ladder (PRD Section 6)."""

from .adequacy import AdequacyEngine, AdequacySystem, assemble_adequacy_system
from .base import Engine
from .cem import CapacityExpansionEngine
from .dynamics import DynamicsEngine, derive_stability_requirements
from .emt import EMTEngine, impedance_scan, lcl_resonance_hz, short_circuit_ratios
from .powerflow import PowerFlowEngine, assemble_pf_case, solve_newton_raphson
from .economic_core import (
    EconomicView,
    EngineOptions,
    assemble_view,
    build_dispatch_model,
)
from .pcm import ProductionCostEngine

ENGINES = {
    "cem": CapacityExpansionEngine,
    "pcm": ProductionCostEngine,
}

__all__ = [
    "Engine",
    "CapacityExpansionEngine",
    "ProductionCostEngine",
    "AdequacyEngine",
    "AdequacySystem",
    "assemble_adequacy_system",
    "PowerFlowEngine",
    "assemble_pf_case",
    "solve_newton_raphson",
    "DynamicsEngine",
    "derive_stability_requirements",
    "EMTEngine",
    "short_circuit_ratios",
    "impedance_scan",
    "lcl_resonance_hz",
    "EconomicView",
    "EngineOptions",
    "assemble_view",
    "build_dispatch_model",
    "ENGINES",
]
