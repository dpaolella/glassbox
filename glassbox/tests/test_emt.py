"""EMT and resonance tests (PRD Sections 6.7, 11.2, 11.3).

Validates the impedance scan against the analytical LCL resonance frequency and
the SCR screen, and the phenomena: a low-SCR grid-following converter is unstable
where the RMS model declared stability, and the scan locates the resonance.
"""

from __future__ import annotations

import warnings

import pytest

from glassbox.engines.emt import (
    impedance_scan,
    lcl_resonance_hz,
    short_circuit_ratios,
    simulate_gfl_thevenin,
    weakest_pocket,
)
from glassbox.scenario import Layer, Scenario, run_scenario
from glassbox.world import build_default_world_with_weather

warnings.filterwarnings("ignore")


# --- analytical validation (Section 11.2) -----------------------------------


@pytest.mark.parametrize("l1,c,l2", [(0.05, 0.03, 0.05), (0.08, 0.02, 0.10),
                                     (0.04, 0.05, 0.06)])
def test_impedance_scan_locates_lcl_resonance(l1, c, l2):
    """The scan's peak matches the analytical LCL resonance frequency."""
    analytic = lcl_resonance_hz(l1, c, l2, base_freq=60.0)
    scan = impedance_scan(l1, c, l2, grid_l=0.0, f_max=max(analytic * 2, 1500),
                          n=1500, base_freq=60.0)
    assert scan.resonance_peaks_hz, "no resonance peak found"
    nearest = min(scan.resonance_peaks_hz, key=lambda p: abs(p - analytic))
    assert abs(nearest - analytic) / analytic < 0.05


def test_gfl_stable_on_strong_grid_unstable_on_weak():
    """Low SCR drives a control instability RMS does not see (6.7)."""
    strong = simulate_gfl_thevenin(scr=5.0, pll_bw_hz=30.0)
    weak = simulate_gfl_thevenin(scr=1.0, pll_bw_hz=30.0)
    assert strong.stable
    assert not weak.stable


def test_scr_decreases_grid_strength_monotonically():
    # higher grid impedance (lower SCR) -> larger steady angle / weaker damping
    s_hi = simulate_gfl_thevenin(scr=5.0)
    s_lo = simulate_gfl_thevenin(scr=0.7)
    assert s_hi.stable and not s_lo.stable


# --- on the default system --------------------------------------------------


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


def test_scr_screen_finds_weak_inverter_pocket(world):
    scr = short_circuit_ratios(world)
    assert scr, "no converter buses found"
    bus, val = weakest_pocket(world)
    # the weakest inverter pocket is genuinely weak (SCR well below 3)
    assert val < 3.0
    assert val == min(scr.values())


def test_emt_engine_demonstrates_weak_grid_instability(world):
    run = run_scenario(world, Scenario(id="emt", layer=Layer.EMT, weather_years=[0],
                                       emt_scr_override=1.2, emt_pll_bw_hz=30.0))
    assert run.summary["gfl_stable"] is False
    assert "instability" in run.summary["verdict"].lower()
    # the impedance scan reports a resonance peak near the analytic value
    assert run.summary["resonance_peaks_hz"]


def test_emt_strong_grid_is_stable(world):
    run = run_scenario(world, Scenario(id="emt", layer=Layer.EMT, weather_years=[0],
                                       emt_scr_override=6.0, emt_pll_bw_hz=20.0))
    assert run.summary["gfl_stable"] is True


def test_rms_to_emt_handoff_selects_pocket(world):
    """The EMT micro-example is seeded from the low-SCR screen (Section 6.8)."""
    run = run_scenario(world, Scenario(id="emt", layer=Layer.EMT, weather_years=[0]))
    bus = run.summary["weak_pocket_bus"]
    assert bus in run.summary["all_scr"]
    # the selected pocket is the weakest one (allow display rounding)
    assert abs(run.summary["short_circuit_ratio"]
               - min(run.summary["all_scr"].values())) < 0.02
