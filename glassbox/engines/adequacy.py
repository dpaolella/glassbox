"""Resource adequacy engine (PRD Section 6.4) — facets: adq, ops (simplified).

Probabilistic reliability over many sampled years and outage draws, built
transparently in numpy (Section 6.4: "build the sampler and accumulator
transparently"). The chronological storage dispatch is simulated hour by hour,
vectorized across draws.

Key ideas:
  * Weather years are sampled from the synthetic generator, so VRE availability
    and load are correlated by construction — dunkelflaute coincident with peak
    load drives loss-of-load (Section 7 / 6.4).
  * Thermal and storage units fail via a two-state (MTTF, MTTR) process.
  * LOLE (loss-of-load expectation, hours/year) and EUE (expected unserved
    energy, MWh/year) accumulate over draws.
  * ELCC (effective load carrying capability) uses the standard iterative
    method with common random numbers so the bisection is clean and monotone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..explain import ExplainPayload, Formulation
from ..schema import AdequacyResult, Provenance, World
from .base import ENGINE_VERSION

HOURS_PER_YEAR = 8760


# --- system description (facet adq) -----------------------------------------


@dataclass
class DispatchUnit:
    id: str
    capacity_mw: float
    mttf_h: float
    mttr_h: float

    @property
    def for_rate(self) -> float:
        """Steady-state forced outage rate MTTR / (MTTF + MTTR)."""
        if self.mttf_h <= 0:
            return 0.0
        return self.mttr_h / (self.mttf_h + self.mttr_h)


@dataclass
class VREUnit:
    id: str
    capacity_mw: float
    profile_id: str          # availability TimeSeries id
    tech: str = "wind"


@dataclass
class StorageFleet:
    power_mw: float = 0.0
    energy_mwh: float = 0.0
    eff_c: float = 0.95
    eff_d: float = 0.95
    soc_min_pu: float = 0.0
    soc_max_pu: float = 1.0


@dataclass
class AdequacySystem:
    dispatchable: list[DispatchUnit]
    vre: list[VREUnit]
    storage: StorageFleet
    # per-available-weather-year arrays (system aggregated, copper-plate)
    year_load: dict[int, np.ndarray]              # year -> (8760,) MW
    year_vre: dict[int, dict[str, np.ndarray]]    # year -> {profile_id: (8760,)}
    weather_years: list[int]


def assemble_adequacy_system(world: World, weather_years: list[int]) -> AdequacySystem:
    """Resolve the world (facet adq) into a copper-plate adequacy system."""
    store = world.time_series_store
    n_years = world.weather_model.n_years if world.weather_model else 1
    years = [y for y in weather_years if 0 <= y < n_years] or [0]

    dispatchable: list[DispatchUnit] = []
    for g in world.generators:
        if g.is_vre or not g.in_service or g.status.value == "retired":
            continue
        # existing dispatchable capacity with a two-state forced-outage process
        cap = g.p_max_mw
        if cap <= 0:
            continue
        dispatchable.append(DispatchUnit(
            id=g.id, capacity_mw=cap,
            mttf_h=g.mttf_h or 2000.0, mttr_h=g.mttr_h or 50.0))
    for h in world.hydro_units:
        if not h.in_service:
            continue
        # reservoir hydro modeled as firm dispatchable capacity (optimistic but
        # standard for a copper-plate adequacy screen)
        dispatchable.append(DispatchUnit(id=h.id, capacity_mw=h.p_max_mw,
                                         mttf_h=4000.0, mttr_h=40.0))

    vre: list[VREUnit] = []
    for g in world.generators:
        if (g.is_vre and g.in_service and g.status.value != "retired"
                and g.availability_profile_id):
            cap = g.p_max_mw
            if cap > 0:
                vre.append(VREUnit(id=g.id, capacity_mw=cap,
                                   profile_id=g.availability_profile_id,
                                   tech=g.technology.value))

    # storage fleet (aggregate power and energy across existing units)
    p = sum(s.p_discharge_max_mw for s in world.storage_units)
    e = sum(s.energy_capacity_mwh for s in world.storage_units)
    eff_c = np.mean([s.efficiency_charge for s in world.storage_units]) if world.storage_units else 0.95
    eff_d = np.mean([s.efficiency_discharge for s in world.storage_units]) if world.storage_units else 0.95
    storage = StorageFleet(power_mw=p, energy_mwh=e, eff_c=float(eff_c), eff_d=float(eff_d))

    # per-year system load and per-profile VRE availability
    year_load: dict[int, np.ndarray] = {}
    year_vre: dict[int, dict[str, np.ndarray]] = {}
    profile_ids = {v.profile_id for v in vre}
    for y in range(n_years):
        sl = slice(y * HOURS_PER_YEAR, (y + 1) * HOURS_PER_YEAR)
        total_load = np.zeros(HOURS_PER_YEAR)
        for ld in world.loads:
            if ld.demand_profile_id and ld.demand_profile_id in store:
                total_load += store.get(ld.demand_profile_id)[sl]
        year_load[y] = total_load
        year_vre[y] = {pid: store.get(pid)[sl] for pid in profile_ids if pid in store}

    return AdequacySystem(dispatchable=dispatchable, vre=vre, storage=storage,
                          year_load=year_load, year_vre=year_vre, weather_years=years)


# --- Monte Carlo ensemble + simulation --------------------------------------


@dataclass
class Ensemble:
    """Pre-sampled stochastics, reused across ELCC evaluations (common random
    numbers) so the bisection on added load is clean and monotone."""

    n_draws: int
    hours: int
    draw_year: np.ndarray                 # (n_draws,) weather-year index per draw
    load: np.ndarray                      # (n_draws, hours) MW
    vre_gen: np.ndarray                   # (n_draws, hours) MW available
    avail_cap: np.ndarray                 # (n_draws, hours) MW dispatchable available
    storage: StorageFleet


def _sample_two_state(mttf_h: float, mttr_h: float, n_draws: int, hours: int,
                      rng: np.random.Generator) -> np.ndarray:
    """Sequential up/down availability (1=up) of shape (n_draws, hours)."""
    out = np.ones((n_draws, hours), dtype=np.float64)
    if mttf_h <= 0 or mttr_h <= 0:
        return out
    for d in range(n_draws):
        t = 0
        up = rng.random() > (mttr_h / (mttf_h + mttr_h))  # start in steady state
        while t < hours:
            if up:
                dur = int(np.ceil(rng.exponential(mttf_h)))
            else:
                dur = int(np.ceil(rng.exponential(mttr_h)))
                out[d, t:min(t + dur, hours)] = 0.0
            t += max(dur, 1)
            up = not up
    return out


def build_ensemble(system: AdequacySystem, n_draws: int, rng: np.random.Generator,
                   hours: int = HOURS_PER_YEAR) -> Ensemble:
    """Pre-sample weather-year assignment, VRE, load, and outage availability."""
    years = system.weather_years
    draw_year = np.array([years[i % len(years)] for i in range(n_draws)])

    load = np.empty((n_draws, hours))
    vre_gen = np.zeros((n_draws, hours))
    for d in range(n_draws):
        y = int(draw_year[d])
        load[d] = system.year_load[y][:hours]
        for v in system.vre:
            prof = system.year_vre[y].get(v.profile_id)
            if prof is not None:
                vre_gen[d] += v.capacity_mw * prof[:hours]

    avail_cap = np.zeros((n_draws, hours))
    for u in system.dispatchable:
        up = _sample_two_state(u.mttf_h, u.mttr_h, n_draws, hours, rng)
        avail_cap += u.capacity_mw * up

    return Ensemble(n_draws=n_draws, hours=hours, draw_year=draw_year, load=load,
                    vre_gen=vre_gen, avail_cap=avail_cap, storage=system.storage)


def simulate(ens: Ensemble, extra_load_mw: float = 0.0,
             extra_firm_mw: float = 0.0, extra_vre: Optional[np.ndarray] = None,
             extra_storage: Optional[StorageFleet] = None) -> dict:
    """Chronological adequacy simulation, vectorized across draws.

    Returns LOLE (h/yr), EUE (MWh/yr), per-draw arrays, and the worst loss
    events. ``extra_*`` inject a test resource for ELCC.
    """
    n, H = ens.n_draws, ens.hours
    firm = ens.avail_cap + extra_firm_mw
    vre = ens.vre_gen + (extra_vre if extra_vre is not None else 0.0)
    load = ens.load + extra_load_mw

    # storage fleet (optionally augmented for ELCC of storage)
    p_max = ens.storage.power_mw
    e_max = ens.storage.energy_mwh
    eff_c, eff_d = ens.storage.eff_c, ens.storage.eff_d
    soc_lo, soc_hi = ens.storage.soc_min_pu, ens.storage.soc_max_pu
    if extra_storage is not None:
        p_max += extra_storage.power_mw
        e_max += extra_storage.energy_mwh

    soc = np.full(n, e_max * soc_hi)         # start full
    lo, hi = e_max * soc_lo, e_max * soc_hi
    eue = np.zeros(n)
    lol_hours = np.zeros(n)
    # track unserved per hour to surface worst events
    worst = np.zeros(n)

    for h in range(H):
        margin = firm[:, h] + vre[:, h] - load[:, h]
        surplus = np.clip(margin, 0, None)
        deficit = np.clip(-margin, 0, None)
        if e_max > 0:
            # charge from surplus
            charge = np.minimum(np.minimum(surplus, p_max), (hi - soc) / max(eff_c, 1e-9))
            soc = soc + charge * eff_c
            # discharge to cover deficit
            discharge = np.minimum(np.minimum(deficit, p_max), (soc - lo) * eff_d)
            soc = soc - discharge / max(eff_d, 1e-9)
        else:
            discharge = np.zeros(n)
        unserved = np.clip(deficit - discharge, 0, None)
        eue += unserved
        lol_hours += (unserved > 1e-6).astype(float)
        worst = np.maximum(worst, unserved)

    return {
        "lole": float(lol_hours.mean()),
        "eue": float(eue.mean()),
        "lol_hours_per_draw": lol_hours,
        "eue_per_draw": eue,
        "worst_shortfall_mw": float(worst.max()),
    }


# --- ELCC (effective load carrying capability) ------------------------------


def elcc(ens: Ensemble, *, firm_mw: float = 0.0, vre: Optional[np.ndarray] = None,
         storage: Optional[StorageFleet] = None, nameplate_mw: float,
         tol_mw: float = 5.0, max_iter: int = 14) -> float:
    """Load (MW) the resource supports at constant reliability (Section 6.4).

    Add the resource, then bisect a uniform load addition until LOLE returns to
    the baseline (without-resource) value. Uses common random numbers (the same
    ensemble), so LOLE is monotone in added load and the bisection is clean.
    """
    base_lole = simulate(ens)["lole"]
    with_res = simulate(ens, extra_firm_mw=firm_mw, extra_vre=vre, extra_storage=storage)
    if with_res["lole"] >= base_lole:
        return 0.0  # resource did not improve reliability
    lo, hi = 0.0, nameplate_mw * 1.2
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        lole = simulate(ens, extra_load_mw=mid, extra_firm_mw=firm_mw,
                        extra_vre=vre, extra_storage=storage)["lole"]
        if lole > base_lole:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol_mw:
            break
    return 0.5 * (lo + hi)


# --- engine ------------------------------------------------------------------


class AdequacyEngine:
    facets = ["adq", "ops"]
    name = "ra"

    def __init__(self, n_draws: int = 40, seed: int = 0,
                 elcc_resource_ids: Optional[list[str]] = None):
        self.n_draws = n_draws
        self.seed = seed
        self.elcc_resource_ids = elcc_resource_ids or []

    def run(self, system: AdequacySystem) -> tuple[AdequacyResult, ExplainPayload]:
        rng = np.random.default_rng(self.seed)
        ens = build_ensemble(system, self.n_draws, rng)
        sim = simulate(ens)

        result = AdequacyResult(engine="ra", engine_version=ENGINE_VERSION,
                                n_draws=self.n_draws)
        result.lole_hours_per_year = sim["lole"]
        result.eue_mwh_per_year = sim["eue"]
        # worst loss events: draws with the most loss hours
        order = np.argsort(sim["lol_hours_per_draw"])[::-1][:5]
        result.loss_events = [
            {"draw": int(i), "weather_year": int(ens.draw_year[i]),
             "loss_hours": float(sim["lol_hours_per_draw"][i]),
             "unserved_mwh": float(sim["eue_per_draw"][i])}
            for i in order if sim["lol_hours_per_draw"][i] > 0]

        # ELCC for requested resources
        for rid in self.elcc_resource_ids:
            val = self._elcc_for(system, ens, rid)
            if val is not None:
                result.elcc_mw[rid] = val

        return result, self._explain(system, ens, sim, result)

    def _elcc_for(self, system: AdequacySystem, ens: Ensemble, rid: str):
        # VRE resource: ELCC via its availability profile
        for v in system.vre:
            if v.id == rid:
                y_avg = np.zeros(ens.hours)
                # use the per-draw weather year availability
                extra = np.zeros((ens.n_draws, ens.hours))
                for d in range(ens.n_draws):
                    prof = system.year_vre[int(ens.draw_year[d])].get(v.profile_id)
                    if prof is not None:
                        extra[d] = v.capacity_mw * prof[: ens.hours]
                return elcc(ens, vre=extra, nameplate_mw=v.capacity_mw)
        for u in system.dispatchable:
            if u.id == rid:
                return elcc(ens, firm_mw=u.capacity_mw * (1 - u.for_rate),
                            nameplate_mw=u.capacity_mw)
        return None

    def _explain(self, system, ens, sim, result) -> ExplainPayload:
        return ExplainPayload(
            title="Resource Adequacy (sequential Monte Carlo): LOLE / EUE / ELCC",
            formulation=Formulation(
                statement=("Sample many weather years (correlated VRE + load) and "
                           "two-state forced outages, dispatch chronologically "
                           "against available capacity, and accumulate loss-of-load "
                           "statistics."),
                symbolic=[
                    "FOR_u = MTTR_u / (MTTF_u + MTTR_u)",
                    "margin_{d,h} = Σ_u avail_{u,d,h}·cap_u + Σ_vre cap·a_{d,h} − load_{d,h}",
                    "soc_{d,h} = soc_{d,h-1} + η_c·charge − discharge/η_d",
                    "unserved_{d,h} = max(load − available − discharge, 0)",
                    "LOLE = mean_d Σ_h 1[unserved>0];  EUE = mean_d Σ_h unserved",
                    "ELCC(R): ΔL s.t. LOLE(S+R, load+ΔL) = LOLE(S)",
                ],
                variables=["forced-outage state per unit", "storage SOC trajectory"],
            ),
            inputs={
                "n_draws": ens.n_draws, "weather_years": system.weather_years,
                "n_dispatchable": len(system.dispatchable), "n_vre": len(system.vre),
                "storage_power_mw": system.storage.power_mw,
                "storage_energy_mwh": system.storage.energy_mwh,
                "total_firm_mw": sum(u.capacity_mw for u in system.dispatchable),
                "peak_load_mw": float(ens.load.max()),
            },
            outputs={
                "LOLE_hours_per_year": result.lole_hours_per_year,
                "EUE_mwh_per_year": result.eue_mwh_per_year,
                "ELCC_mw": result.elcc_mw,
                "worst_shortfall_mw": sim["worst_shortfall_mw"],
            },
            intermediates={
                "loss_events": result.loss_events,
                "forced_outage_rates": {u.id: round(u.for_rate, 4)
                                        for u in system.dispatchable},
            },
            provenance={"engine": "ra", "version": ENGINE_VERSION,
                        "input_facets": self.facets,
                        "note": "VRE adequacy via correlated weather draws, not FOR"},
        )
