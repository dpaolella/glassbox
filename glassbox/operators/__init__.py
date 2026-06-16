"""Projection operators (PRD Section 5).

Three operators turn the one stored world into the view a layer consumes:
spatial, temporal, attribute. Each implements explain() and surfaces where it
loses information.
"""

from .attribute import AttributeProjection
from .base import Operator
from .spatial import SpatialMode, SpatialProjection, SpatialView
from .temporal import (
    TemporalProjection,
    TemporalView,
    build_full_chronology_map,
    build_representative_days_map,
)

__all__ = [
    "Operator",
    "AttributeProjection",
    "SpatialProjection", "SpatialMode", "SpatialView",
    "TemporalProjection", "TemporalView",
    "build_full_chronology_map", "build_representative_days_map",
]
