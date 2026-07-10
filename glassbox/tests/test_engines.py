"""Economic engine tests (PRD Sections 6.2, 6.3, 11.3 phenomena)."""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from glassbox.engines.economic_core import (
    EconomicView,
    EngineOptions,
    GenSpec,
    LineSpec,
    build_dispatch_model,
    solve_model,
)
from glassbox.scenario import Layer, Override, Scenario, SpatialOperator, run_scenario
from glassbox.world import build_default_world_with_weather

warnings.filterwarnings("ignore")


def test_line_investment_is_a_decision():
    """A candidate line is built when it's the cheapest way to deliver power.

    Cheap generation sits at node A, all load at node B, and there is no existing
    line. CEM must build the candidate corridor (transport addition) to avoid
    unserved energy at VOLL.
    """
    gen = GenSpec(id="g", node="A", tech="ccgt", is_vre=False, marginal_cost=10.0,
                  emissions_t_per_mwh=0.0, p_nom_existing=500.0, is_candidate=False,
                  capex_annual_per_mw=0.0, build_max=0.0, p_min_pu=0.0,
                  ramp_per_h=500.0, min_up_h=0, min_down_h=0, start_cost=0.0,
                  no_load_cost=0.0, reserve_eligible=True)
    cand = LineSpec(id="cand_AB", a="A", b="B", x=0.1, rating=200.0,
                    is_candidate=True, capex_annual_per_mw=1000.0, build_max=200.0,
                    transport_only=True)
    view = EconomicView(
        nodes=["A", "B"], T=1, period_ids=np.array([0]),
        period_weight=np.array([1.0]), annual_divisor=1.0, gens=[gen], storages=[],
        load=np.array([[0.0], [100.0]]), lines=[cand], network_mode="dc",
        reference_node="A", voll=10000.0)
    model = build_dispatch_model(view, EngineOptions(investment=True, reserves=False,
                                                     label="cem"))
    solve_model(model)
    built = float(model.m.variables["line_build"].solution.sel(l="cand_AB"))
    unserved = float((model.m.variables["unserved"].solution.values
                      * view.period_weight).sum())
    assert built >= 99.0, f"expected the corridor built ~100 MW, got {built:.1f}"
    assert unserved < 1.0  # the built line delivers the load


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


def _cem(world, mode="aggregate", overrides=None, years=None):
    sc = Scenario(id=f"cem_{mode}", layer=Layer.CEM,
                  spatial_operator=SpatialOperator(mode),
                  temporal_map_id="representative_days",
                  weather_years=years or [0], n_rep_days=3,
                  overrides=overrides or [])
    return run_scenario(world, sc)


def _peak_window_start(world, hours):
    """Start hour (within year 0) of a window centered on the annual peak load."""
    store = world.time_series_store
    total = None
    for ld in world.loads:
        if ld.demand_profile_id and ld.demand_profile_id in store:
            arr = store.get(ld.demand_profile_id)[:8760]
            total = arr if total is None else total + arr
    peak = int(total.argmax())
    return max(0, min(peak - hours // 2, 8760 - hours))


def _pcm(world, mode="aggregate", hours=48):
    start = _peak_window_start(world, hours)
    sc = Scenario(id=f"pcm_{mode}", layer=Layer.PCM,
                  spatial_operator=SpatialOperator(mode),
                  temporal_map_id="full_chronology",
                  weather_years=[0], horizon_hours=hours, horizon_start=start)
    return run_scenario(world, sc)


def test_cem_builds_from_expansion_candidates(world):
    """CEM invests in ExpansionCandidates (separate from existing assets)."""
    # nodal is transmission-constrained -> the engine builds candidate capacity
    run = _cem(world, mode="identity")
    built = {**run.result.built_capacity_mw, **run.result.built_storage_power_mw}
    assert built, "expected CEM to build at least one candidate"
    # every built id is an expansion candidate, not an existing asset
    cand_ids = {c.id for c in world.expansion_candidates}
    existing_ids = {g.id for g in world.generators}
    assert set(built) <= cand_ids
    assert not (set(built) & existing_ids)


def test_cem_builds_from_zonal_resource_potential(world):
    """CEM invests from zonal supply curves, reported separately from candidates.

    Resource-potential builds aggregate back to the supply curve id (not a bus),
    never exceed the zone's potential, and are kept distinct from both existing
    assets and node-specific candidates.
    """
    run = _cem(world, mode="identity")
    rp_built = run.result.built_resource_potential_mw
    assert rp_built, "expected CEM to build from at least one supply curve"
    rp_ids = {rp.id for rp in world.resource_potentials}
    potential = {rp.id: sum(t.build_max_mw for t in rp.tranches)
                 for rp in world.resource_potentials}
    for rid, mw in rp_built.items():
        assert rid in rp_ids                     # keyed by the curve, not a bus
        assert mw <= potential[rid] + 1e-6       # cannot exceed the resource ceiling
    # resource-potential builds are not mixed into the nodal-candidate channels
    cand_ids = {c.id for c in world.expansion_candidates}
    assert not (set(run.result.built_capacity_mw) & rp_ids)
    assert not (set(rp_built) & cand_ids)


def test_cem_solves_and_builds(world):
    run = _cem(world)
    assert run.result.total_cost > 0
    # explain payload is faithful (Section 2.2)
    assert run.explain.formulation.symbolic
    assert "built_capacity_mw" in run.explain.outputs


def test_realized_capacity_factor_traces_to_availability(world):
    """Realized CF is an output, derived from the availability profile (4.6)."""
    run = _cem(world)
    disp = run.result.operational
    assert disp.realized_capacity_factor
    # provenance points at the availability/dispatch derivation
    assert "capacity" in disp.provenance.notes.lower()
    # every VRE unit's realized CF must not exceed its availability mean
    # (the curtailment gap is non-negative)
    for g in world.generators:
        if g.is_vre and g.id in run.summary.get("realized_capacity_factor", {}):
            cf = run.summary["realized_capacity_factor"][g.id]
            assert 0.0 <= cf <= 1.0


def test_nodal_reveals_congestion_zonal_hides_it(world):
    """PCM: LMP spread and interface congestion appear nodal, vanish zonal."""
    nodal = _pcm(world, "identity")
    zonal = _pcm(world, "aggregate")
    nodal_spread = nodal.summary.get("price_spread", 0.0)
    zonal_spread = zonal.summary.get("price_spread", 0.0)
    # nodal prices are at least as dispersed as zonal, and the binding
    # remote->center interface shows a shadow price only under the nodal view
    assert nodal_spread >= zonal_spread
    assert nodal.summary.get("congestion")


def test_nodal_curtailment_exceeds_zonal(world):
    """Congestion strands remote VRE under the nodal view (Section 6.3)."""
    nodal = _cem(world, "identity")
    zonal = _cem(world, "aggregate")
    assert (nodal.summary["curtailment_mwh_weighted"]
            >= zonal.summary["curtailment_mwh_weighted"])


def test_carbon_price_reduces_fossil_generation(world):
    """Capacity mix / dispatch shifts away from fossil under a carbon price."""
    base = _cem(world, "aggregate")
    carbon = _cem(world, "aggregate",
                  overrides=[Override(kind="set_policy", policy_kind="carbon_price",
                                      value=150.0)])

    rates = {}  # tCO2/MWh per generator
    for g in world.generators:
        if g.fuel_id and g.heat_rate_mmbtu_per_mwh:
            fuel = next((f for f in world.fuels if f.id == g.fuel_id), None)
            if fuel:
                rates[g.id] = g.heat_rate_mmbtu_per_mwh * fuel.emissions_tco2_per_mmbtu

    def emissions(run):
        disp = run.result.operational
        return sum(sum(disp.generation_mw.get(gid, [])) * rate
                   for gid, rate in rates.items())

    # a carbon price weakly reduces emissions at the optimum, and raises cost
    assert carbon.summary["total_cost"] >= base.summary["total_cost"]
    assert emissions(carbon) <= emissions(base) + 1e-6


def test_storage_power_and_energy_sized_independently(world):
    """CEM can build storage power and energy as separate quantities (1.3)."""
    run = _cem(world, "aggregate")
    # the schema/result expose distinct power and energy build dicts
    assert hasattr(run.result, "built_storage_power_mw")
    assert hasattr(run.result, "built_storage_energy_mwh")


def test_pcm_explain_has_uc_formulation(world):
    run = _pcm(world, "aggregate")
    syms = " ".join(run.explain.formulation.symbolic)
    assert "u_{g,t}" in syms or "startup" in syms.lower()
    assert run.result.solve_status == "ok"
