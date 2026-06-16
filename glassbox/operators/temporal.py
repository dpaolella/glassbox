"""Temporal projection operator (PRD Section 5.2).

``TemporalProjection(world, temporal_map) -> reduced time series``

A TemporalMap defines which timestamps map to which representative period, with
weights. We provide several maps: full chronology, representative days (k-means
over the multi-year series with peak preservation), an event window (dynamics),
and a microsecond trace (EMT). We surface in explain() that representative-period
reduction destroys chronology, which is why storage and ramping are mispriced
under it (a Section 1.3 lesson). The one-year-vs-many demonstration is two
temporal maps over the same multi-year record (and the ``weather_years`` filter
on the Scenario, Section 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..explain import ExplainPayload, Formulation
from ..schema import TemporalMap, TemporalMapKind
from .base import Operator


@dataclass
class TemporalView:
    map_id: str
    kind: str
    timesteps: np.ndarray            # source indices retained, in order
    weights: np.ndarray              # weight per retained timestep
    chronological: bool
    # reduce any source array to the representative timesteps
    def reduce(self, array: np.ndarray) -> np.ndarray:
        return array[self.timesteps]


@dataclass
class TemporalProjectionResult:
    view: TemporalView
    stats: dict[str, Any] = field(default_factory=dict)


def build_representative_days_map(
    series_2d: np.ndarray,
    n_days: int = 12,
    hours_per_day: int = 24,
    seed: int = 0,
    preserve_peak: bool = True,
) -> TemporalMap:
    """k-means-style clustering of daily profiles with peak preservation.

    ``series_2d`` is (n_features, n_hours) — typically stacked load/VRE signals.
    Days are clustered on their hourly shape; the medoid day of each cluster is
    chosen as representative, weighted by cluster size. The peak day is always
    retained as its own representative period (peak preservation).
    """
    n_features, n_hours = series_2d.shape
    n_total_days = n_hours // hours_per_day
    usable = n_total_days * hours_per_day
    # daily feature vectors: flatten each day's multi-feature hourly shape
    day_feats = (series_2d[:, :usable]
                 .reshape(n_features, n_total_days, hours_per_day)
                 .transpose(1, 0, 2)
                 .reshape(n_total_days, n_features * hours_per_day))

    rng = np.random.default_rng(seed)
    k = min(n_days, n_total_days)
    # simple k-means++ init
    centers = [day_feats[rng.integers(n_total_days)]]
    for _ in range(1, k):
        d2 = np.min([np.sum((day_feats - c) ** 2, axis=1) for c in centers], axis=0)
        probs = d2 / d2.sum() if d2.sum() > 0 else None
        centers.append(day_feats[rng.choice(n_total_days, p=probs)])
    centers = np.array(centers)
    labels = np.zeros(n_total_days, dtype=int)
    for _ in range(25):
        dists = np.linalg.norm(day_feats[:, None, :] - centers[None, :, :], axis=2)
        labels = dists.argmin(axis=1)
        for c in range(k):
            mask = labels == c
            if mask.any():
                centers[c] = day_feats[mask].mean(axis=0)

    rep_days: list[int] = []
    weights: list[float] = []
    for c in range(k):
        members = np.where(labels == c)[0]
        if len(members) == 0:
            continue
        # medoid: member closest to its center
        d = np.linalg.norm(day_feats[members] - centers[c], axis=1)
        medoid = int(members[d.argmin()])
        rep_days.append(medoid)
        weights.append(float(len(members)))

    if preserve_peak:
        peak_hour = int(np.argmax(series_2d.sum(axis=0)[:usable]))
        peak_day = peak_hour // hours_per_day
        if peak_day not in rep_days:
            rep_days.append(peak_day)
            weights.append(1.0)

    # expand to hourly representative timesteps
    rep_timesteps: list[int] = []
    period_of_ts = [-1] * usable
    for p, day in enumerate(rep_days):
        for h in range(hours_per_day):
            rep_timesteps.append(day * hours_per_day + h)
    # assign every source day to its representative period for the map
    rep_day_index = {day: i for i, day in enumerate(rep_days)}
    for d in range(n_total_days):
        # representative period that this day was clustered into
        lbl = labels[d]
        # find a rep day in this cluster
        cluster_reps = [rd for rd in rep_days if labels[rd] == lbl]
        target = rep_day_index[cluster_reps[0]] if cluster_reps else 0
        for h in range(hours_per_day):
            period_of_ts[d * hours_per_day + h] = target

    return TemporalMap(
        id=f"rep_days_{len(rep_days)}",
        name=f"{len(rep_days)} representative days",
        kind=TemporalMapKind.REPRESENTATIVE_DAYS,
        representative_timesteps=rep_timesteps,
        period_weights=[w for w in weights for _ in range(hours_per_day)],
        period_of_timestep=period_of_ts,
        chronological=False,
    )


def build_full_chronology_map(n_hours: int) -> TemporalMap:
    return TemporalMap(
        id="full_chronology",
        name="full chronology",
        kind=TemporalMapKind.FULL_CHRONOLOGY,
        representative_timesteps=list(range(n_hours)),
        period_weights=[1.0] * n_hours,
        period_of_timestep=list(range(n_hours)),
        chronological=True,
    )


class TemporalProjection(Operator):
    name = "temporal"

    def __init__(self, temporal_map: TemporalMap):
        self.map = temporal_map
        self._view: TemporalView | None = None

    def apply(self, world=None, **kwargs) -> TemporalView:
        ts = np.asarray(self.map.representative_timesteps, dtype=int)
        if self.map.period_weights and len(self.map.period_weights) == len(ts):
            w = np.asarray(self.map.period_weights, dtype=float)
        else:
            w = np.ones(len(ts))
        self._view = TemporalView(
            map_id=self.map.id,
            kind=self.map.kind.value,
            timesteps=ts,
            weights=w,
            chronological=self.map.chronological,
        )
        return self._view

    def explain(self) -> ExplainPayload:
        loss: list[str] = []
        if self.map.kind == TemporalMapKind.FULL_CHRONOLOGY:
            loss = ["None: full chronology is preserved (lossless in time)."]
            statement = "Identity in time: keep every source timestep, weight 1."
        else:
            loss = [
                "Chronology is destroyed: representative periods are reordered "
                "and reweighted, so inter-period storage cycling and multi-day "
                "ramping events cannot be represented. Storage and long-duration "
                "balancing are systematically mispriced (Section 1.3).",
            ]
            statement = ("Map source timesteps onto a small set of weighted "
                         "representative periods (clustering). Weighted sums "
                         "approximate annual totals, but time order is lost.")
        n = len(self._view.timesteps) if self._view else len(self.map.representative_timesteps)
        return ExplainPayload(
            title=f"Temporal projection: {self.map.kind.value}",
            formulation=Formulation(
                statement=statement,
                symbolic=["reduced[p] = source[rep_timestep[p]]",
                          "annual_total ≈ sum_p weight[p] * reduced[p]"],
            ),
            inputs={"map_id": self.map.id, "kind": self.map.kind.value},
            outputs={"n_representative_timesteps": n,
                     "chronological": self.map.chronological},
            intermediates={"total_weight": float(np.sum(self._view.weights))
                           if self._view is not None else None},
            information_loss=loss,
        )
