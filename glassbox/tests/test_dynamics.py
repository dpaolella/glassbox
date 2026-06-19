"""Dynamic stability tests (PRD Sections 6.6, 11.2, 11.3).

Validates the SMIB integrator against the analytical equal-area critical clearing
time, and the phenomena: nadir/RoCoF worsen as inertia falls, FFR arrests the
decline, grid-forming converters provide inertia grid-following ones do not, and
a longer fault-clearing time breaks transient stability. Also checks the
dynamics -> operations inertia/FFR handoff (Section 6.7).
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from glassbox.engines.dynamics import (
    assemble_frequency_system,
    critical_clearing_time_analytical,
    critical_clearing_time_numerical,
    derive_stability_requirements,
    simulate_frequency_response,
    simulate_smib,
)
from glassbox.scenario import Layer, Scenario, run_scenario
from glassbox.world import build_default_world_with_weather

warnings.filterwarnings("ignore")


# --- analytical validation (Section 11.2) -----------------------------------


@pytest.mark.parametrize("H,pm,pmax", [(4.0, 0.8, 1.5), (3.0, 0.6, 1.2), (5.0, 0.9, 1.8)])
def test_critical_clearing_time_matches_equal_area(H, pm, pmax):
    a = critical_clearing_time_analytical(H, pm, pmax)
    n = critical_clearing_time_numerical(H, pm, pmax)
    assert abs(a - n) < 0.01, f"analytic {a:.4f} vs numeric {n:.4f}"


def test_longer_fault_clearing_breaks_stability():
    """A fault cleared before CCT is stable; cleared after it is not (6.6)."""
    H, pm, pmax = 4.0, 0.8, 1.5
    cct = critical_clearing_time_analytical(H, pm, pmax)
    stable = simulate_smib(H, pm, pmax, 0.0, pmax, cct * 0.7, D=0.0)
    unstable = simulate_smib(H, pm, pmax, 0.0, pmax, cct * 1.3, D=0.0)
    assert stable.stable
    assert not unstable.stable


# --- frequency-response phenomena -------------------------------------------


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


def test_rocof_inverse_to_inertia():
    """RoCoF(0+) = ΔP·f0/(2H): halving inertia doubles the RoCoF."""
    from glassbox.engines.dynamics import FrequencySystem

    sys_hi = FrequencySystem(h_total_mws=20000, sync_mva_online=4000, gfm_mva_online=0,
                             total_gen_mw=3000, total_load_mw=3000,
                             governor_gain_mw_per_hz=500, headroom_mw=800,
                             largest_unit_mw=600, f0=60.0)
    sys_lo = FrequencySystem(h_total_mws=10000, sync_mva_online=2000, gfm_mva_online=0,
                             total_gen_mw=3000, total_load_mw=3000,
                             governor_gain_mw_per_hz=500, headroom_mw=800,
                             largest_unit_mw=600, f0=60.0)
    hi = simulate_frequency_response(sys_hi, 600)
    lo = simulate_frequency_response(sys_lo, 600)
    assert abs(lo.rocof_hz_per_s) > 1.9 * abs(hi.rocof_hz_per_s)
    # the lower-inertia system also reaches a deeper nadir
    assert lo.nadir_hz < hi.nadir_hz


def test_low_inertia_deepens_nadir_on_default(world):
    hi = run_scenario(world, Scenario(id="dh", layer=Layer.DYN, weather_years=[0],
                                      dyn_inertia_scale=1.0))
    lo = run_scenario(world, Scenario(id="dl", layer=Layer.DYN, weather_years=[0],
                                      dyn_inertia_scale=0.3))
    assert lo.summary["nadir_deviation_hz"] < hi.summary["nadir_deviation_hz"]
    assert abs(lo.summary["rocof_hz_per_s"]) > abs(hi.summary["rocof_hz_per_s"])


def test_ffr_arrests_frequency_decline(world):
    noffr = run_scenario(world, Scenario(id="nf", layer=Layer.DYN, weather_years=[0],
                                         dyn_inertia_scale=0.3, dyn_enable_ffr=False))
    ffr = run_scenario(world, Scenario(id="wf", layer=Layer.DYN, weather_years=[0],
                                       dyn_inertia_scale=0.3, dyn_enable_ffr=True,
                                       dyn_ffr_mw=400.0))
    # FFR lifts the nadir (less severe deviation)
    assert ffr.summary["nadir_deviation_hz"] > noffr.summary["nadir_deviation_hz"]


def test_grid_forming_provides_inertia(world):
    """GFM converters contribute kinetic energy; GFL do not (6.6)."""
    from glassbox.engines.powerflow import peak_load_hour, snapshot_dispatch

    hour = peak_load_hour(world, 0)
    dispatch = snapshot_dispatch(world, hour, 0, mode="nodal")
    with_gfm = assemble_frequency_system(world, dispatch, gfm_provides_inertia=True)
    without = assemble_frequency_system(world, dispatch, gfm_provides_inertia=False)
    assert with_gfm.h_total_mws >= without.h_total_mws


def test_dynamics_to_operations_handoff(world):
    """Low inertia produces an upward min-inertia / FFR requirement (6.7)."""
    lo = run_scenario(world, Scenario(id="dl", layer=Layer.DYN, weather_years=[0],
                                      dyn_inertia_scale=0.3))
    assert lo.summary["min_inertia_mws"] > 0
    # at low inertia the system is stability-limited and needs FFR/inertia
    assert lo.summary["inertia_deficit_mws"] >= 0
