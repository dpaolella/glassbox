"""Named weather events, auto-detected from the stored ensemble (issue #34).

Scans the generated multi-year series for the episodes planners actually fear
— the longest dunkelflaute (dark doldrums: low wind AND low solar), the worst
sustained demand peak, and (for contrast) the best wind week — and names them,
so the UI can say "The January Lull, year 7" and offer to run it through the
production-cost model as a one-click stress test.
"""

from __future__ import annotations

import numpy as np

from ..schema import World

HOURS_PER_YEAR = 8760

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
_MONTH_STARTS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def _month_of(hour_in_year: int) -> str:
    day = (hour_in_year // 24) % 365
    m = 0
    for i, start in enumerate(_MONTH_STARTS):
        if day >= start:
            m = i
    return _MONTHS[m]


def _rolling_mean(x: np.ndarray, window: int) -> np.ndarray:
    """Rolling mean; index i covers x[i : i+window]."""
    c = np.cumsum(np.insert(x, 0, 0.0))
    return (c[window:] - c[:-window]) / window


def _locate(idx: int, window: int, n_years: int) -> tuple[int, int]:
    """(year, start-hour-within-year), window clamped inside one year."""
    year = min(idx // HOURS_PER_YEAR, n_years - 1)
    start = idx - year * HOURS_PER_YEAR
    start = max(0, min(start, HOURS_PER_YEAR - window))
    return year, start


def detect_events(world: World) -> list[dict]:
    """Scan the ensemble; return named stress/showcase events with a
    ready-to-run nodal PCM scenario for each."""
    store = world.time_series_store
    n_years = world.weather_model.n_years if world.weather_model else 1

    wind, solar, demand = [], [], []
    for ts_id, ts in store.series.items():
        arr = None
        if ts.kind.value == "availability":
            arr = store.get(ts_id)
            (wind if "wind" in ts_id else solar).append(arr)
        elif ts.kind.value == "demand":
            demand.append(store.get(ts_id))
    if not demand or not (wind or solar):
        return []

    vre = np.zeros_like(demand[0], dtype=float)
    if wind:
        vre += np.mean(wind, axis=0)
    if solar:
        vre += np.mean(solar, axis=0)
    vre /= (2 if wind and solar else 1)
    load = np.sum(demand, axis=0)

    def scenario(year: int, start: int, hours: int) -> dict:
        return {
            "id": "stress_event", "layer": "pcm",
            "spatial_operator": "identity",
            "temporal_map_id": "full_chronology",
            "weather_years": [int(year)],
            "horizon_hours": int(hours), "horizon_start": int(start),
        }

    events: list[dict] = []

    # deepest 72h dunkelflaute (combined VRE availability minimum)
    w = 72
    r = _rolling_mean(vre, w)
    i = int(np.argmin(r))
    yr, st = _locate(i, w, n_years)
    events.append({
        "key": "dunkelflaute",
        "name": f"The {_month_of(st)} Lull (year {yr})",
        "description": (f"The deepest {w // 24}-day dark doldrums in the "
                        f"{n_years}-year ensemble: combined wind+solar "
                        f"availability averages {r[i]:.0%}. This is the event "
                        "that sizes storage and firm capacity."),
        "kind": "dunkelflaute", "year": yr, "start_hour": st,
        "duration_h": w, "severity": round(float(r[i]), 4),
        "scenario": scenario(yr, st, w),
    })

    # worst 72h sustained demand (heat wave / cold snap)
    r = _rolling_mean(load, w)
    i = int(np.argmax(r))
    yr, st = _locate(i, w, n_years)
    season = _month_of(st)
    label = "Heat Wave" if season in ("June", "July", "August", "September") \
        else "Cold Snap"
    events.append({
        "key": "peak_stress",
        "name": f"The {season} {label} (year {yr})",
        "description": (f"The highest sustained {w // 24}-day demand in the "
                        f"ensemble: load averages {r[i]:,.0f} MW. The "
                        "adequacy layer's tail risk lives in windows like "
                        "this one."),
        "kind": "peak_stress", "year": yr, "start_hour": st,
        "duration_h": w, "severity": round(float(r[i]), 1),
        "scenario": scenario(yr, st, w),
    })

    # best wind week (the showcase — negative prices / curtailment territory)
    if wind:
        w2 = 168
        wind_mean = np.mean(wind, axis=0)
        r = _rolling_mean(wind_mean, w2)
        i = int(np.argmax(r))
        yr, st = _locate(i, w2, n_years)
        events.append({
            "key": "wind_week",
            "name": f"The Great {_month_of(st)} Blow (year {yr})",
            "description": (f"The windiest week in the ensemble: availability "
                            f"averages {r[i]:.0%}. Watch the corridor pin at "
                            "its limit and remote energy get curtailed."),
            "kind": "wind_week", "year": yr, "start_hour": st,
            "duration_h": w2, "severity": round(float(r[i]), 4),
            "scenario": scenario(yr, st, w2),
        })
    return events
