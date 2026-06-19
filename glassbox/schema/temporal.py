"""Representative-period definitions (PRD Section 5.2).

A ``TemporalMap`` defines which timestamps map to which representative period,
with weights. It is stored on the World (``World.temporal_maps``) and consumed by
the temporal projection operator (Section 5.2). Surfacing that
representative-period reduction destroys chronology is a Section 1.3 lesson.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from .facets import facet_field


class TemporalMapKind(str, Enum):
    FULL_CHRONOLOGY = "full_chronology"
    REPRESENTATIVE_DAYS = "representative_days"
    EVENT_WINDOW = "event_window"
    EMT_TRACE = "emt_trace"


class TemporalMap(BaseModel):
    """Maps source timesteps onto representative periods with weights.

    ``period_of_timestep[t]`` gives the representative-period index that source
    timestep ``t`` is assigned to. ``period_weights[p]`` is the number of source
    timesteps the representative period stands in for (so weighted sums recover
    annual totals). For full chronology the map is the identity and all weights
    are 1.
    """

    id: str = facet_field(facets=["core"])
    name: str = facet_field(facets=["core"], default="")
    kind: TemporalMapKind = facet_field(facets=["core"], default=TemporalMapKind.FULL_CHRONOLOGY)

    # for reduced maps: representative timesteps selected from the source record
    representative_timesteps: list[int] = facet_field(
        facets=["core", "ops", "inv"], default_factory=list,
        description="indices into the source series that define each rep period")
    period_weights: list[float] = facet_field(
        facets=["core", "ops", "inv"], default_factory=list,
        description="weight (source-timestep count) per representative period")
    # optional explicit assignment of each source timestep -> rep-period index
    period_of_timestep: list[int] = facet_field(
        facets=["core"], default_factory=list,
        description="rep-period index for each source timestep (clustering map)")
    chronological: bool = facet_field(
        facets=["core"], default=True,
        description="whether the reduced series preserves chronological order")

    # window-style maps (dynamics/emt): an explicit [start, end] in source units
    window_start: int | None = facet_field(facets=["core"], default=None)
    window_end: int | None = facet_field(facets=["core"], default=None)
    resolution_s: float | None = facet_field(
        facets=["dyn", "emt"], unit="s", default=None,
        description="sub-hourly step for event-window / emt maps")
