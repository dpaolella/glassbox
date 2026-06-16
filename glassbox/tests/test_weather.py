"""Synthetic weather generator tests (PRD Sections 7, 2.4).

These encode the *ground-truth* phenomena the generator must produce by
construction: wind-load anticorrelation / dunkelflaute, and inter-annual spread
that makes a single year misrepresent the truth.
"""

from __future__ import annotations

import numpy as np

from glassbox.schema import TimeSeriesStore, WeatherModelParams, WeatherSite
from glassbox.weather import WeatherGenerator


def _make_gen(seed=1, n_years=5):
    params = WeatherModelParams(seed=seed, n_years=n_years, hours_per_year=8760,
                                latitude_deg=40.0)
    sites = [
        WeatherSite(id="w1", kind="wind", x=0, y=0),
        WeatherSite(id="w2", kind="wind", x=50, y=0),
        WeatherSite(id="s1", kind="solar", x=0, y=0),
        WeatherSite(id="l1", kind="load", x=0, y=0, scale=1000.0),
    ]
    return WeatherGenerator(params, sites)


def test_shapes_and_reproducibility():
    g1 = _make_gen(seed=7)
    s1 = TimeSeriesStore()
    g1.generate(s1)
    g2 = _make_gen(seed=7)
    s2 = TimeSeriesStore()
    g2.generate(s2)
    a1 = s1.get("availability__w1")
    a2 = s2.get("availability__w1")
    assert a1.shape == (5 * 8760,)
    np.testing.assert_allclose(a1, a2)  # same seed -> identical


def test_availability_in_unit_interval():
    g = _make_gen()
    store = TimeSeriesStore()
    g.generate(store)
    for sid in ("availability__w1", "availability__s1"):
        arr = store.get(sid)
        assert arr.min() >= 0.0 and arr.max() <= 1.0


def test_solar_is_zero_at_night():
    g = _make_gen()
    store = TimeSeriesStore()
    g.generate(store)
    solar = store.get("availability__s1")
    # midnight hours across the record should be ~0
    midnight = solar[0::24]
    assert midnight.max() < 1e-6


def test_wind_load_anticorrelation_in_winter():
    # Blocking-high regime suppresses wind and raises winter load by design.
    g = _make_gen(n_years=4)
    store = TimeSeriesStore()
    g.generate(store)
    wind = store.get("availability__w1")
    load = store.get("demand__l1")
    # restrict to winter hours of year 0
    hours = np.arange(8760)
    day = hours // 24
    winter = (day < 80) | (day > 310)
    corr = np.corrcoef(wind[:8760][winter], load[:8760][winter])[0, 1]
    assert corr < 0.0, f"expected wind-load anticorrelation in winter, got {corr}"


def test_interannual_spread_exists():
    # Single-year means must differ -> one year misrepresents the truth.
    g = _make_gen(n_years=8)
    store = TimeSeriesStore()
    gt = g.generate(store)
    means = gt.per_year_means("w1")
    assert max(means) - min(means) > 0.01


def test_spatial_correlation_between_nearby_sites():
    g = _make_gen()
    store = TimeSeriesStore()
    g.generate(store)
    w1 = store.get("availability__w1")
    w2 = store.get("availability__w2")
    corr = np.corrcoef(w1, w2)[0, 1]
    assert corr > 0.3, f"nearby wind sites should correlate, got {corr}"
