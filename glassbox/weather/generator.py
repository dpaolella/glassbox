"""Synthetic weather generator (PRD Section 7).

Generates multi-year, correlated, hourly weather with *known ground-truth
statistics* and no external data. Because the tool defines the ground-truth
distribution, it can show exactly how badly a single sampled year misrepresents
it (Section 2.4) — the airtight multi-weather-year argument.

Design (Section 7):
  * Latent weather-regime Markov chain (calm high-pressure, windy frontal,
    mixed). A blocking-high regime suppresses wind and, in winter, raises load,
    producing dunkelflaute and wind-load anticorrelation *by construction*.
  * Deterministic seasonal + diurnal cycles for irradiance, temperature, load.
  * Stochastic per-site OU noise with temporal persistence, modulated by regime.
  * Spatial correlation by distance via a covariance matrix + Cholesky.
  * Inter-annual variability: each year a fresh draw, plus a slow oscillation.

Outputs go into the World's TimeSeriesStore (Section 4.5.19) and a
``GroundTruth`` object exposes the true marginal/joint distributions for the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..schema import (
    TimeSeries,
    TimeSeriesKind,
    TimeSeriesStore,
    WeatherModelParams,
    WeatherSite,
)

HOURS_PER_DAY = 24


@dataclass
class GroundTruth:
    """The true distributions the generator samples from (Section 7 outputs)."""

    site_ids: list[str]
    site_kinds: list[str]
    # full multi-year hourly arrays, per site id
    availability: dict[str, np.ndarray] = field(default_factory=dict)
    demand: dict[str, np.ndarray] = field(default_factory=dict)
    regime_sequence: np.ndarray | None = None
    n_years: int = 0
    hours_per_year: int = 8760

    def marginal(self, site_id: str, bins: int = 50) -> dict:
        """True marginal distribution (histogram) over all years for a site."""
        arr = self.availability.get(site_id)
        if arr is None:
            arr = self.demand[site_id]
        hist, edges = np.histogram(arr, bins=bins, density=True)
        return {"bin_edges": edges.tolist(), "density": hist.tolist(),
                "mean": float(arr.mean()), "std": float(arr.std())}

    def per_year_means(self, site_id: str) -> list[float]:
        """Mean per simulated year — shows inter-annual spread (one vs many)."""
        arr = self.availability.get(site_id, self.demand.get(site_id))
        yearly = arr.reshape(self.n_years, self.hours_per_year)
        return [float(y.mean()) for y in yearly]


class WeatherGenerator:
    """Generates weather and binds it to the World's time-series store."""

    def __init__(self, params: WeatherModelParams, sites: list[WeatherSite]):
        self.p = params
        self.sites = sites
        # Stable, decoupled streams (issue #11): regimes, annual draws, and
        # each site's noise use independent generators, so adding/removing a
        # site leaves every other series bit-identical (reproducible worlds,
        # stable tests). Site streams are keyed by a hash of the site id.
        self.rng = np.random.default_rng(params.seed)  # legacy/general draws
        self._rng_regimes = np.random.default_rng(
            np.random.SeedSequence([params.seed, 101]))
        self._rng_annual = np.random.default_rng(
            np.random.SeedSequence([params.seed, 202]))
        self.n_regimes = len(params.regime_names)
        self.transition = self._regime_transition_matrix()

    # --- regime Markov chain ---------------------------------------------

    def _regime_transition_matrix(self) -> np.ndarray:
        if self.p.regime_transition:
            T = np.asarray(self.p.regime_transition, dtype=float)
        else:
            # Persistent regimes: high self-transition, the rest spread evenly.
            n = self.n_regimes
            T = np.full((n, n), 0.0)
            persist = 0.92
            for i in range(n):
                T[i, :] = (1 - persist) / (n - 1)
                T[i, i] = persist
        T = T / T.sum(axis=1, keepdims=True)
        return T

    def _sample_regimes(self, n_hours: int) -> np.ndarray:
        """Sample an hourly regime sequence from the Markov chain."""
        seq = np.empty(n_hours, dtype=int)
        # start from stationary-ish: regime 0
        state = 0
        for t in range(n_hours):
            seq[t] = state
            state = self._rng_regimes.choice(self.n_regimes, p=self.transition[state])
        return seq

    # --- spatial correlation ---------------------------------------------

    def _cholesky_for(self, site_subset: list[WeatherSite]) -> np.ndarray:
        """Lower-Cholesky factor of the exponential-decay covariance matrix."""
        n = len(site_subset)
        if n == 0:
            return np.zeros((0, 0))
        coords = np.array([[s.x, s.y] for s in site_subset], dtype=float)
        L0 = self.p.correlation_length_km
        cov = np.empty((n, n))
        for i in range(n):
            for j in range(n):
                d = np.hypot(*(coords[i] - coords[j]))
                cov[i, j] = np.exp(-d / max(L0, 1e-9))
        # jitter for positive-definiteness
        cov += np.eye(n) * 1e-8
        return np.linalg.cholesky(cov)

    def _site_rng(self, site_id: str) -> np.random.Generator:
        """A generator whose stream depends only on (seed, site id)."""
        import hashlib

        h = int.from_bytes(hashlib.sha256(site_id.encode()).digest()[:8], "little")
        return np.random.default_rng(np.random.SeedSequence([self.p.seed, 303, h]))

    def _correlated_ou(self, sites: list[WeatherSite], n_hours: int,
                       regimes: np.ndarray) -> np.ndarray:
        """OU process per site with spatial correlation, modulated by regime.

        Returns array (n_sites, n_hours) of zero-mean persistent noise. Each
        site draws its raw shocks from its own id-keyed stream, and the
        Cholesky mixing runs in id-sorted order — so a site's series only
        changes if a new site sorts *before* it, and adding sites at the end
        of the id order leaves existing series bit-identical (issue #11).
        """
        n = len(sites)
        if n == 0:
            return np.empty((0, n_hours))
        order = sorted(range(n), key=lambda i: sites[i].id)
        sorted_sites = [sites[i] for i in order]
        L = self._cholesky_for(sorted_sites)
        # raw shocks: one independent, id-keyed stream per site
        raw = np.vstack([
            self._site_rng(s.id).standard_normal(n_hours) for s in sorted_sites
        ])
        theta = self.p.ou_theta
        sigma = self.p.ou_sigma
        regime_amp = np.linspace(0.7, 1.4, self.n_regimes)
        amp_t = regime_amp[regimes] * sigma
        x = np.zeros(n)
        out_sorted = np.empty((n, n_hours))
        for t in range(n_hours):
            x = x + theta * (-x) + amp_t[t] * (L @ raw[:, t])
            out_sorted[:, t] = x
        # back to the caller's site order
        out = np.empty((n, n_hours))
        for k, i in enumerate(order):
            out[i, :] = out_sorted[k, :]
        return out

    # --- deterministic cycles --------------------------------------------

    def _diurnal_solar(self, n_hours: int) -> np.ndarray:
        """Clear-sky irradiance factor in [0,1] from latitude/day/hour."""
        hour = np.arange(n_hours) % HOURS_PER_DAY
        day = (np.arange(n_hours) // HOURS_PER_DAY)
        lat = np.deg2rad(self.p.latitude_deg)
        decl = np.deg2rad(23.45) * np.sin(2 * np.pi * (day - 81) / 365.0)
        hour_angle = np.deg2rad((hour - 12) * 15.0)
        cos_zenith = (np.sin(lat) * np.sin(decl)
                      + np.cos(lat) * np.cos(decl) * np.cos(hour_angle))
        return np.clip(cos_zenith, 0.0, None)

    def _seasonal_temp(self, n_hours: int) -> np.ndarray:
        """Temperature deviation from comfort (deg C), seasonal + diurnal."""
        t = np.arange(n_hours)
        day = t / 24.0
        seasonal = -12.0 * np.cos(2 * np.pi * day / 365.0)  # cold winter
        diurnal = 4.0 * np.sin(2 * np.pi * (t % 24) / 24.0 - np.pi / 2)
        return seasonal + diurnal

    def _diurnal_load_shape(self, n_hours: int) -> np.ndarray:
        """Base load shape (per-unit of mean) from daily/weekly/seasonal."""
        t = np.arange(n_hours)
        hour = t % 24
        dow = (t // 24) % 7
        daily = 0.85 + 0.25 * np.sin(2 * np.pi * (hour - 7) / 24.0)
        weekly = np.where(dow >= 5, 0.92, 1.0)
        seasonal = 1.0 + 0.12 * np.cos(2 * np.pi * (t / 24.0) / 365.0)
        return daily * weekly * seasonal

    # --- conversion ------------------------------------------------------

    def _wind_power(self, noise_row: np.ndarray, regimes: np.ndarray) -> np.ndarray:
        """Synthetic wind speed -> power curve -> per-unit availability."""
        # Mean wind speed depends on regime: windy_frontal high, calm_high low.
        regime_mean_ws = np.linspace(4.0, 11.0, self.n_regimes)
        ws = regime_mean_ws[regimes] + 2.5 * noise_row
        ws = np.clip(ws, 0.0, None)
        ci, rated, co = self.p.wind_cut_in_ms, self.p.wind_rated_ms, self.p.wind_cut_out_ms
        p = np.zeros_like(ws)
        ramp = (ws >= ci) & (ws < rated)
        p[ramp] = ((ws[ramp] - ci) / (rated - ci)) ** 3
        p[(ws >= rated) & (ws < co)] = 1.0
        p[ws >= co] = 0.0
        return np.clip(p, 0.0, 1.0)

    def _solar_power(self, clear_sky: np.ndarray, noise_row: np.ndarray,
                     regimes: np.ndarray) -> np.ndarray:
        """Clear-sky irradiance x regime-driven cloudiness -> availability."""
        # cloudiness: calm_high clear (~0.9), frontal cloudy (~0.4)
        regime_clear = np.linspace(0.92, 0.45, self.n_regimes)
        cloud = np.clip(regime_clear[regimes] + 0.1 * noise_row, 0.05, 1.0)
        return np.clip(clear_sky * cloud, 0.0, 1.0)

    def _load(self, shape: np.ndarray, temp: np.ndarray, regimes: np.ndarray,
              year_idx: int, n_hours: int) -> np.ndarray:
        """Base shape + temperature response + growth, regime-coupled."""
        heating = self.p.temp_heating_coef * np.clip(-temp, 0.0, None)
        cooling = self.p.temp_cooling_coef * np.clip(temp - 5.0, 0.0, None)
        # blocking-high (regime 0) in winter raises load -> wind-load anticorr.
        winter = (np.arange(n_hours) // 24 % 365 < 90) | (np.arange(n_hours) // 24 % 365 > 305)
        block_high = (regimes == 0) & winter
        regime_load = np.where(block_high, 0.08, 0.0)
        growth = (1 + self.p.load_growth_per_year) ** year_idx
        demand = self.p.load_base_mw * (shape + heating + cooling + regime_load) * growth
        return np.clip(demand, 0.0, None)

    # --- top-level generation -------------------------------------------

    def generate(self, store: TimeSeriesStore) -> GroundTruth:
        """Generate all site series into ``store`` and return ground truth."""
        n_years = self.p.n_years
        hpy = self.p.hours_per_year
        n_hours = n_years * hpy

        regimes = self._sample_regimes(n_hours)
        clear_sky = self._diurnal_solar(n_hours)
        temp = self._seasonal_temp(n_hours)
        load_shape = self._diurnal_load_shape(n_hours)

        wind_sites = [s for s in self.sites if s.kind == "wind"]
        solar_sites = [s for s in self.sites if s.kind == "solar"]
        load_sites = [s for s in self.sites if s.kind == "load"]

        wind_noise = self._correlated_ou(wind_sites, n_hours, regimes)
        solar_noise = self._correlated_ou(solar_sites, n_hours, regimes)
        load_noise = self._correlated_ou(load_sites, n_hours, regimes)

        # inter-annual multiplier (slow oscillation + per-year draw)
        years = np.arange(n_years)
        slow = 1.0 + 0.05 * np.sin(2 * np.pi * years / 7.0)
        annual_draw = 1.0 + self.p.interannual_sigma * self._rng_annual.standard_normal(n_years)
        annual = (slow * annual_draw)
        annual_hourly = np.repeat(annual, hpy)

        gt = GroundTruth(
            site_ids=[s.id for s in self.sites],
            site_kinds=[s.kind for s in self.sites],
            regime_sequence=regimes,
            n_years=n_years,
            hours_per_year=hpy,
        )
        years_list = list(range(n_years))

        for i, s in enumerate(wind_sites):
            avail = self._wind_power(wind_noise[i], regimes)
            avail = np.clip(avail * annual_hourly * (s.scale or 1.0), 0.0, 1.0)
            self._store(store, s, avail, TimeSeriesKind.AVAILABILITY, "pu", years_list, hpy)
            gt.availability[s.id] = avail

        for i, s in enumerate(solar_sites):
            avail = self._solar_power(clear_sky, solar_noise[i], regimes)
            avail = np.clip(avail * (s.scale or 1.0), 0.0, 1.0)
            self._store(store, s, avail, TimeSeriesKind.AVAILABILITY, "pu", years_list, hpy)
            gt.availability[s.id] = avail

        for i, s in enumerate(load_sites):
            # apply year growth per-year then concatenate
            chunks = []
            for y in range(n_years):
                sl = slice(y * hpy, (y + 1) * hpy)
                chunks.append(
                    self._load(load_shape[sl] + 0.05 * load_noise[i][sl],
                               temp[sl], regimes[sl], y, hpy))
            demand = np.concatenate(chunks) * annual_hourly
            # Normalize so the first-year mean equals this site's scale (MW);
            # the slow growth trend then lifts later years above it. This keeps
            # absolute MW interpretable regardless of shape inflation.
            first_year_mean = demand[:hpy].mean()
            if first_year_mean > 0:
                demand = demand * (s.scale / first_year_mean)
            self._store(store, s, demand, TimeSeriesKind.DEMAND, "MW", years_list, hpy)
            gt.demand[s.id] = demand

        # store the regime sequence as an inspectable series too
        rseries = TimeSeries(id="regime", kind=TimeSeriesKind.REGIME, unit="index",
                             years=years_list, hours_per_year=hpy)
        store.add(rseries, regimes.astype(float))
        return gt

    def _store(self, store: TimeSeriesStore, site: WeatherSite, arr: np.ndarray,
               kind: TimeSeriesKind, unit: str, years: list[int], hpy: int) -> None:
        ts = TimeSeries(id=f"{kind.value}__{site.id}", kind=kind, unit=unit,
                        years=years, hours_per_year=hpy)
        store.add(ts, arr)
