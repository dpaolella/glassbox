"""Modeling engines, one per rung of the abstraction ladder (PRD Section 6)."""

from .base import Engine
from .cem import CapacityExpansionEngine
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
    "EconomicView",
    "EngineOptions",
    "assemble_view",
    "build_dispatch_model",
    "ENGINES",
]
