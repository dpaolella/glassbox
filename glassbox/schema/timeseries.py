"""TimeSeries metadata and the multi-year time-series store (PRD 4.5.19, 4.4).

Arrays are stored out-of-line (parquet/npz) and referenced by id, so the static
schema objects stay small. The TimeSeries object here holds only metadata; the
actual array lives in the TimeSeriesStore.
"""

from __future__ import annotations

from enum import Enum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .facets import facet_field


class TimeSeriesKind(str, Enum):
    AVAILABILITY = "availability"
    DEMAND = "demand"
    INFLOW = "inflow"
    FUEL_PRICE = "fuel_price"
    REGIME = "regime"
    OTHER = "other"


class TimeSeries(BaseModel):
    """Metadata for a multi-year series; the array is referenced by id."""

    id: str = facet_field(facets=["core"])
    kind: TimeSeriesKind = facet_field(facets=["core"], default=TimeSeriesKind.OTHER)
    unit: str | None = facet_field(facets=["core"], default=None)
    resolution: str = facet_field(facets=["core"], default="hourly")
    start: str = facet_field(facets=["core"], default="2030-01-01T00:00:00",
                             description="ISO timestamp of first sample")
    length: int = facet_field(facets=["core"], default=0,
                              description="number of samples")
    years: list[int] = facet_field(facets=["core", "adq"], default_factory=list,
                                   description="weather-year labels spanned")
    hours_per_year: int = facet_field(facets=["core"], default=8760)


class TimeSeriesStore(BaseModel):
    """In-memory store of arrays keyed by TimeSeries id.

    Persisted to parquet/npz separately from the JSON schema (Section 3.3). Held
    here as plain numpy for live engine consumption; serialization is handled by
    ``world/serialize.py``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    series: dict[str, TimeSeries] = Field(default_factory=dict)
    arrays: dict[str, np.ndarray] = Field(default_factory=dict, exclude=True)

    def add(self, ts: TimeSeries, array: np.ndarray) -> None:
        ts.length = int(array.shape[0])
        self.series[ts.id] = ts
        self.arrays[ts.id] = np.asarray(array, dtype=float)

    def get(self, ts_id: str) -> np.ndarray:
        return self.arrays[ts_id]

    def meta(self, ts_id: str) -> TimeSeries:
        return self.series[ts_id]

    def __contains__(self, ts_id: str) -> bool:
        return ts_id in self.arrays
