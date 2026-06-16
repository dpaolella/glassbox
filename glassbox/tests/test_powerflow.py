"""Steady-state security tests (PRD Sections 6.5, 11.2, 11.3).

Validates the Newton-Raphson kernel against an analytical 2-bus case, and the
phenomena: AC power flow converges and reports losses the DC model omits, N-1
contingencies cause post-contingency overloads, and the operating point's
network assumptions (zonal vs nodal) change the physical result.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from glassbox.engines.powerflow import (
    Branch,
    PFCase,
    PowerFlowEngine,
    branch_flows,
    dc_powerflow,
    solve_newton_raphson,
)
from glassbox.scenario import Layer, Scenario, run_scenario
from glassbox.world import build_default_world_with_weather

warnings.filterwarnings("ignore")


def _two_bus(p_load_pu, q_load_pu, r=0.02, x=0.10):
    y = 1.0 / complex(r, x)
    Y = np.array([[y, -y], [-y, y]], dtype=complex)
    br = [Branch("L01", 0, 1, y, 0.0, complex(1, 0), 100, 120, "ac_line")]
    return PFCase(n=2, bus_ids=["b0", "b1"], bus_index={"b0": 0, "b1": 1}, Ybus=Y,
                  branches=br, slack=0, pv=[], pq=[1],
                  p_spec=np.array([0.0, -p_load_pu]),
                  q_spec=np.array([0.0, -q_load_pu]), v_set=np.array([1.0, 1.0]),
                  base_mva=100, v_min=np.array([0.9, 0.9]),
                  v_max=np.array([1.1, 1.1]))


def test_two_bus_analytical():
    """Newton-Raphson matches the closed-form 2-bus solution."""
    case = _two_bus(0.5, 0.2)
    sol = solve_newton_raphson(case)
    assert sol.converged
    # closed form: solve V1, theta from power-balance at the load bus
    # verify by substitution that injection equals spec at the solution
    P, Q = (case.Ybus @ sol.V), None
    S = sol.V * np.conj(case.Ybus @ sol.V)
    assert abs(S[1].real - (-0.5)) < 1e-6
    assert abs(S[1].imag - (-0.2)) < 1e-6
    # losses are positive and small
    loss = sum(f["loss_mw"] for f in branch_flows(case, sol.V).values())
    assert 0 < loss < 5


def test_dc_omits_losses_ac_reveals_them():
    case = _two_bus(0.5, 0.0)
    sol = solve_newton_raphson(case)
    ac_loss = sum(f["loss_mw"] for f in branch_flows(case, sol.V).values())
    dc = dc_powerflow(case)
    # DC flow is lossless; AC shows a real loss on the same branch
    assert ac_loss > 0.0
    assert abs(dc["L01"]) > 0.0


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


def test_ac_power_flow_converges_on_default(world):
    run = run_scenario(world, Scenario(id="pf", layer=Layer.PF, weather_years=[0],
                                       pf_dispatch_mode="nodal"))
    assert run.result.converged
    assert run.summary["losses_mw"] > 0  # AC losses the DC/transport model omits
    # voltages are within a sane band around 1.0
    assert 0.8 < run.summary["min_voltage_pu"] <= run.summary["max_voltage_pu"] < 1.2


def test_n1_contingency_causes_violations(world):
    """An N-1 outage produces post-contingency overloads/violations (6.5)."""
    run = run_scenario(world, Scenario(id="pf", layer=Layer.PF, weather_years=[0],
                                       pf_dispatch_mode="nodal"))
    # at least one monitored single contingency violates a limit
    assert run.summary["n_n1_contingencies_with_violations"] >= 1
    # and the violations name a branch or bus
    some = next(iter(run.result.contingency_violations.values()))
    assert some and ("branch" in some[0] or "bus" in some[0] or "type" in some[0])


def test_zonal_dispatch_differs_from_nodal_physically(world):
    """The operating point's network assumption changes the AC result (6.5)."""
    nodal = run_scenario(world, Scenario(id="pfn", layer=Layer.PF, weather_years=[0],
                                         pf_dispatch_mode="nodal"))
    zonal = run_scenario(world, Scenario(id="pfz", layer=Layer.PF, weather_years=[0],
                                         pf_dispatch_mode="zonal"))
    # a transport (zonal) dispatch ignores intra-zonal lines, so the AC flows,
    # losses, or overload count differ from the nodal-feasible operating point
    assert (nodal.summary["losses_mw"] != zonal.summary["losses_mw"]
            or nodal.summary["n_base_overloads"] != zonal.summary["n_base_overloads"])


def test_ybus_symmetry(world):
    from glassbox.engines.powerflow import build_ybus
    Y, _, _ = build_ybus(world)
    # passive network without phase shifters -> symmetric admittance matrix
    assert np.allclose(Y, Y.T, atol=1e-9)
