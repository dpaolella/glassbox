"""Resource adequacy tests (PRD Sections 6.4, 11.2, 11.3).

Covers the phenomena (single year understates tail risk; ELCC of VRE declines
with penetration; storage ELCC depends on duration) and the analytical
small-case validation (LOLP vs binomial convolution).
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

from glassbox.engines.adequacy import (
    AdequacySystem,
    DispatchUnit,
    StorageFleet,
    VREUnit,
    assemble_adequacy_system,
    build_ensemble,
    elcc,
    simulate,
)
from glassbox.scenario import Layer, Scenario, run_scenario
from glassbox.world import build_default_world_with_weather

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


# --- analytical validation (Section 11.2) -----------------------------------


def test_lolp_matches_binomial_convolution():
    """N identical units, constant load: time-average LOLP ≈ binomial tail.

    5 units x 100 MW, load 350 MW -> system fails when >=2 units are down.
    With forced-outage rate q=0.1: LOLP = P(#down >= 2).
    """
    N, C, L, q = 5, 100.0, 350.0, 0.1
    # MTTF/MTTR giving FOR=q with frequent transitions for good mixing
    mttf, mttr = 90.0, 10.0
    assert abs(mttr / (mttf + mttr) - q) < 1e-9

    units = [DispatchUnit(id=f"u{i}", capacity_mw=C, mttf_h=mttf, mttr_h=mttr)
             for i in range(N)]
    const_load = np.full(8760, L)
    system = AdequacySystem(
        dispatchable=units, vre=[], storage=StorageFleet(),
        year_load={0: const_load}, year_vre={0: {}}, weather_years=[0])

    rng = np.random.default_rng(0)
    ens = build_ensemble(system, n_draws=80, rng=rng)
    lolp_mc = simulate(ens)["lole"] / 8760.0

    # analytical: P(#down >= 2) for Binomial(5, 0.1)
    analytic = 1.0 - sum(math.comb(N, k) * q**k * (1 - q) ** (N - k) for k in (0, 1))
    assert abs(lolp_mc - analytic) < 0.02, f"MC {lolp_mc:.4f} vs analytic {analytic:.4f}"


def test_forced_outage_rate_definition():
    u = DispatchUnit(id="x", capacity_mw=100, mttf_h=950, mttr_h=50)
    assert abs(u.for_rate - 0.05) < 1e-9


# --- phenomena on the default system ----------------------------------------


def test_lole_eue_computed(world):
    run = run_scenario(world, Scenario(id="ra", layer=Layer.RA,
                                       weather_years=[0, 1, 2], ra_n_draws=20,
                                       ra_seed=1))
    assert run.result.lole_hours_per_year >= 0.0
    assert run.result.eue_mwh_per_year >= 0.0
    assert run.explain.formulation.symbolic


def test_single_year_understates_tail_risk(world):
    """A single (benign) weather year understates LOLE vs many years (6.4)."""
    one = run_scenario(world, Scenario(id="ra1", layer=Layer.RA, weather_years=[0],
                                       ra_n_draws=30, ra_seed=1))
    many = run_scenario(world, Scenario(id="raN", layer=Layer.RA,
                                        weather_years=list(range(10)),
                                        ra_n_draws=60, ra_seed=1))
    assert many.summary["lole_hours_per_year"] > one.summary["lole_hours_per_year"]


def test_firm_capacity_elcc_near_nameplate(world):
    system = assemble_adequacy_system(world, list(range(8)))
    ens = build_ensemble(system, 40, np.random.default_rng(3))
    u = max(system.dispatchable, key=lambda d: d.capacity_mw)  # nuclear
    val = elcc(ens, firm_mw=u.capacity_mw * (1 - u.for_rate), nameplate_mw=u.capacity_mw)
    # firm capacity carries ~full credit (within MC + bisection tolerance)
    assert val > 0.85 * u.capacity_mw


def test_storage_elcc_increases_with_duration(world):
    system = assemble_adequacy_system(world, list(range(8)))
    ens = build_ensemble(system, 40, np.random.default_rng(3))
    e2 = elcc(ens, storage=StorageFleet(power_mw=100, energy_mwh=200), nameplate_mw=100)
    e8 = elcc(ens, storage=StorageFleet(power_mw=100, energy_mwh=800), nameplate_mw=100)
    assert e8 >= e2


def test_vre_elcc_declines_with_penetration(world):
    """Incremental wind ELCC is lower when the system already has more VRE."""
    system = assemble_adequacy_system(world, list(range(8)))
    ens = build_ensemble(system, 40, np.random.default_rng(5))
    wind = next(v for v in system.vre if v.tech == "wind")

    def wind_block(scale):
        extra = np.zeros((ens.n_draws, ens.hours))
        for d in range(ens.n_draws):
            prof = system.year_vre[int(ens.draw_year[d])].get(wind.profile_id)
            if prof is not None:
                extra[d] = scale * wind.capacity_mw * prof
        return extra

    # low penetration: ELCC of a wind block on the base system
    elcc_low = elcc(ens, vre=wind_block(1.0), nameplate_mw=wind.capacity_mw)
    # high penetration: saturate the system with wind, then measure the next block
    base_vre = wind_block(4.0)
    base_lole = simulate(ens, extra_vre=base_vre)["lole"]

    # marginal ELCC at high penetration via bisection on the saturated system
    def lole_at(extra_load, extra_vre):
        return simulate(ens, extra_load_mw=extra_load, extra_vre=extra_vre)["lole"]

    block = wind_block(1.0)
    lo, hi = 0.0, wind.capacity_mw
    with_block_lole = lole_at(0.0, base_vre + block)
    if with_block_lole >= base_lole:
        elcc_high = 0.0
    else:
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            if lole_at(mid, base_vre + block) > base_lole:
                hi = mid
            else:
                lo = mid
        elcc_high = 0.5 * (lo + hi)

    assert elcc_high <= elcc_low + 1e-6
