"""Oracle round-trip tests (PRD Sections 11.1, 11.2).

Each engine's transparent kernel is checked against a mature library on the
default system, within tolerance. These tests skip automatically when the
heavy oracle dependency is not installed, so the core suite stays light.
"""

from __future__ import annotations

import warnings

import pytest

from glassbox.engines.powerflow import peak_load_hour
from glassbox.validation.oracles.andes_oracle import HAVE_ANDES, compare_swing_frequency
from glassbox.validation.oracles.pandapower_oracle import (
    HAVE_PANDAPOWER,
    compare_power_flow,
)
from glassbox.validation.oracles.pypsa_oracle import HAVE_PYPSA, compare_dispatch
from glassbox.validation.oracles.pypsa_view_oracle import (
    compare_dispatch_window,
    compare_expansion,
)
from glassbox.world import build_default_world_with_weather

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


# --- pandapower: AC power flow (Section 6.5) ---------------------------------


@pytest.mark.skipif(not HAVE_PANDAPOWER, reason="pandapower not installed")
def test_power_flow_matches_pandapower(world):
    hour = peak_load_hour(world, 0)
    cmp = compare_power_flow(world, hour, 0, dispatch_mode="nodal")
    assert cmp.converged_both
    # the hand-built Newton-Raphson reproduces pandapower's solution closely
    assert cmp.max_v_diff_pu < 1e-4
    assert cmp.max_angle_diff_deg < 1e-2
    assert cmp.max_flow_diff_mw < 1.0
    assert abs(cmp.losses_glassbox_mw - cmp.losses_pandapower_mw) < 1.0


@pytest.mark.skipif(not HAVE_PANDAPOWER, reason="pandapower not installed")
def test_power_flow_matches_pandapower_offpeak(world):
    cmp = compare_power_flow(world, 200, 0, dispatch_mode="nodal")
    assert cmp.converged_both
    assert cmp.max_v_diff_pu < 1e-4


# --- PyPSA: economic dispatch (Sections 6.2, 6.3) ---------------------------


@pytest.mark.skipif(not HAVE_PYPSA, reason="pypsa not installed")
def test_dispatch_matches_pypsa(world):
    for hour in (peak_load_hour(world, 0), 100, 4000):
        cmp = compare_dispatch(world, hour, 0)
        assert cmp.objective_rel_diff < 1e-4, f"objective mismatch at hour {hour}"
        assert cmp.max_dispatch_diff_mw < 1.0, f"dispatch mismatch at hour {hour}"


# --- PyPSA: multi-hour window + expansion (issue #14) ------------------------


@pytest.mark.skipif(not HAVE_PYPSA, reason="pypsa not installed")
def test_dispatch_window_matches_pypsa(world):
    """Zonal 72h window with cyclic storage, the real hydro energy budget and
    congested inter-zonal corridors — both sides must agree everywhere."""
    cmp = compare_dispatch_window(world, start=0, hours=72)
    assert cmp.objective_rel_diff < 1e-5
    assert abs(cmp.unserved_glassbox_mwh - cmp.unserved_pypsa_mwh) < 1.0
    mwh_tol = max(10.0, 0.001 * cmp.total_load_energy_mwh)
    assert cmp.max_gen_energy_diff_mwh < mwh_tol
    # the window must actually EXERCISE the broadened coverage, not just carry it
    assert cmp.storage_throughput_glassbox_mwh > 1.0, "storage never cycled"
    assert abs(cmp.storage_throughput_glassbox_mwh
               - cmp.storage_throughput_pypsa_mwh) < mwh_tol
    assert cmp.hydro_budget_mwh is not None, "hydro budget missing from both sides"
    assert abs(cmp.hydro_energy_glassbox_mwh - cmp.hydro_energy_pypsa_mwh) < mwh_tol
    assert cmp.corridor_congested_hours > 0, "transfer limits never binding"


@pytest.mark.skipif(not HAVE_PYPSA, reason="pypsa not installed")
def test_expansion_matches_pypsa(world):
    """Capacity expansion vs PyPSA p_nom_extendable: same total cost AND the
    same built MW per candidate (generators, tranches, transmission)."""
    cmp = compare_expansion(world, start=0, hours=96)
    assert cmp.objective_rel_diff < 1e-5
    assert cmp.total_built_glassbox_mw > 1.0, "nothing built — comparison is vacuous"
    build_tol = max(1.0, 0.005 * cmp.total_built_glassbox_mw)
    assert cmp.max_build_diff_mw < build_tol
    assert abs(cmp.total_built_glassbox_mw - cmp.total_built_pypsa_mw) < build_tol
    # honesty check: candidate storage is excluded and REPORTED, never silent
    assert cmp.excluded_candidate_storage, "default world has candidate storage"


# --- Andes: RMS dynamics swing (Section 6.6) --------------------------------


@pytest.mark.skipif(not HAVE_ANDES, reason="andes not installed")
def test_swing_frequency_matches_andes():
    cmp = compare_swing_frequency(h=4.0, p=0.8, xd1=0.3, xline=0.3)
    # Andes, the analytical linearized swing, and the transparent integrator all
    # agree on the rotor-angle oscillation frequency (within damping)
    assert cmp.rel_diff_andes_vs_analytic < 0.05
    assert cmp.rel_diff_glassbox_vs_andes < 0.08
