"""Scenario run orchestration (PRD Sections 3.4, 10).

Applies overrides + projection operators, resolves the temporal sampling for the
selected weather years, assembles the engine's numeric view, runs the engine, and
packages the result, the explain() payload, and a comparable summary for diffing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..engines import (
    AdequacyEngine,
    ENGINES,
    PowerFlowEngine,
    assemble_adequacy_system,
    assemble_view,
)
from ..operators import (
    SpatialMode,
    SpatialProjection,
    build_representative_days_map,
)
from ..schema import TimeSeriesStore, World
from .scenario import Layer, Override, Scenario

HOURS_PER_YEAR = 8760


@dataclass
class ScenarioRun:
    scenario: Scenario
    result: Any
    explain: Any
    summary: dict[str, Any] = field(default_factory=dict)
    operator_explanations: dict[str, Any] = field(default_factory=dict)


# --- overrides --------------------------------------------------------------


def _clone_world(world: World) -> World:
    """Deep-copy the schema while sharing the (large) time-series arrays."""
    store = world.time_series_store
    world.time_series_store = TimeSeriesStore()
    try:
        clone = world.model_copy(deep=True)
    finally:
        world.time_series_store = store
    clone.time_series_store = store
    return clone


def apply_overrides(world: World, overrides: list[Override]) -> World:
    w = _clone_world(world)
    for ov in overrides:
        if ov.kind == "set_policy":
            for pol in w.policies:
                if pol.kind.value == ov.policy_kind:
                    pol.value = ov.value
        elif ov.kind == "set_field" and ov.collection and ov.id and ov.field:
            for item in getattr(w, ov.collection):
                if item.id == ov.id:
                    setattr(item, ov.field, ov.value)
        elif ov.kind == "scale_field" and ov.collection and ov.field:
            for item in getattr(w, ov.collection):
                if ov.id is None or item.id == ov.id:
                    cur = getattr(item, ov.field)
                    if cur is not None:
                        setattr(item, ov.field, cur * (ov.factor or 1.0))
        elif ov.kind == "retire" and ov.collection and ov.id:
            for item in getattr(w, ov.collection):
                if item.id == ov.id and hasattr(item, "in_service"):
                    item.in_service = False
    return w


# --- temporal resolution ----------------------------------------------------


def _selected_abs(weather_years: list[int], n_years_avail: int) -> np.ndarray:
    years = [y for y in weather_years if 0 <= y < n_years_avail] or [0]
    blocks = [y * HOURS_PER_YEAR + np.arange(HOURS_PER_YEAR) for y in years]
    return np.concatenate(blocks)


def _resolve_temporal(world: World, scenario: Scenario):
    """Return (abs_timesteps, period_ids, period_weight, annual_divisor)."""
    store = world.time_series_store
    n_years_avail = world.weather_model.n_years if world.weather_model else 1
    selected_abs = _selected_abs(scenario.weather_years, n_years_avail)
    n_sel_years = max(len(scenario.weather_years), 1)

    if scenario.temporal_map_id == "full_chronology":
        # chronological window (PCM): one period, unit weights, no annualization.
        start = scenario.horizon_start
        end = min(start + scenario.horizon_hours, len(selected_abs))
        abs_ts = selected_abs[start:end]
        T = len(abs_ts)
        return abs_ts, np.zeros(T, dtype=int), np.ones(T), 1.0

    # representative days (CEM default): cluster load + VRE signals.
    signals = []
    for ts_id, ts in store.series.items():
        if ts.kind.value in ("availability", "demand"):
            signals.append(store.get(ts_id)[selected_abs])
    if not signals:
        T = len(selected_abs)
        return selected_abs, np.zeros(T, dtype=int), np.ones(T), float(n_sel_years)

    series = np.vstack(signals)
    tmap = build_representative_days_map(series, n_days=scenario.n_rep_days, seed=0)
    rep = np.asarray(tmap.representative_timesteps, dtype=int)
    abs_ts = selected_abs[rep]
    weight = np.asarray(tmap.period_weights, dtype=float)  # hours represented / timestep
    # period id = representative-day index (groups of 24)
    period_ids = np.repeat(np.arange(len(rep) // 24 + 1), 24)[: len(rep)]
    return abs_ts, period_ids, weight, float(n_sel_years)


# --- run --------------------------------------------------------------------


def run_scenario(world: World, scenario: Scenario) -> ScenarioRun:
    w = apply_overrides(world, scenario.overrides)

    # Resource adequacy is a Monte Carlo simulation, not a projected optimization;
    # it samples weather years internally rather than via the temporal operator.
    if scenario.layer == Layer.RA:
        return _run_adequacy(w, scenario)
    if scenario.layer == Layer.PF:
        return _run_powerflow(w, scenario)

    spatial = SpatialProjection(
        SpatialMode.AGGREGATE if scenario.spatial_operator.value == "aggregate"
        else SpatialMode.IDENTITY)
    spatial_view = spatial.apply(w)

    abs_ts, period_ids, weight, divisor = _resolve_temporal(w, scenario)

    investment = scenario.layer == Layer.CEM
    econ_view = assemble_view(w, spatial_view, abs_ts, period_ids, weight, divisor,
                              investment=investment)

    engine = ENGINES[scenario.layer.value]()
    result, explain = engine.run(econ_view)

    summary = _summarize(scenario, econ_view, result)
    return ScenarioRun(
        scenario=scenario, result=result, explain=explain, summary=summary,
        operator_explanations={
            "spatial": spatial.explain().model_dump(mode="json"),
        })


def _run_adequacy(world: World, scenario: Scenario) -> ScenarioRun:
    system = assemble_adequacy_system(world, scenario.weather_years)
    engine = AdequacyEngine(n_draws=scenario.ra_n_draws, seed=scenario.ra_seed,
                            elcc_resource_ids=scenario.ra_elcc_resource_ids)
    result, explain = engine.run(system)
    summary = {
        "layer": "ra",
        "weather_years": scenario.weather_years,
        "n_draws": result.n_draws,
        "lole_hours_per_year": round(result.lole_hours_per_year, 3),
        "eue_mwh_per_year": round(result.eue_mwh_per_year, 1),
        "elcc_mw": {k: round(v, 1) for k, v in result.elcc_mw.items()},
        "n_loss_events": len(result.loss_events),
        "firm_mw": sum(u.capacity_mw for u in system.dispatchable),
        "vre_nameplate_mw": sum(v.capacity_mw for v in system.vre),
    }
    return ScenarioRun(scenario=scenario, result=result, explain=explain,
                       summary=summary, operator_explanations={})


def _run_powerflow(world: World, scenario: Scenario) -> ScenarioRun:
    year = scenario.weather_years[0] if scenario.weather_years else 0
    engine = PowerFlowEngine(hour=scenario.pf_hour, weather_year=year,
                             run_contingencies=scenario.pf_run_contingencies,
                             dispatch_mode=scenario.pf_dispatch_mode)
    result, explain = engine.run(world)
    overloads = explain.outputs.get("base_case_overloads_pct", {})
    summary = {
        "layer": "pf",
        "dispatch_mode": scenario.pf_dispatch_mode,
        "converged": result.converged,
        "iterations": result.iterations,
        "losses_mw": round(result.losses_mw, 2),
        "min_voltage_pu": explain.outputs.get("min_voltage_pu"),
        "max_voltage_pu": explain.outputs.get("max_voltage_pu"),
        "n_base_overloads": len(overloads),
        "base_overloads_pct": {k: round(v, 1) for k, v in overloads.items()},
        "n1_violations": result.contingency_violations,
        "n_n1_contingencies_with_violations": len(result.contingency_violations),
    }
    return ScenarioRun(scenario=scenario, result=result, explain=explain,
                       summary=summary, operator_explanations={})


def _summarize(scenario: Scenario, view, result) -> dict[str, Any]:
    """Comparable metrics for the scenario diff (Section 9.5)."""
    s: dict[str, Any] = {"layer": scenario.layer.value,
                         "spatial": scenario.spatial_operator.value,
                         "weather_years": scenario.weather_years,
                         "n_nodes": len(view.nodes), "n_timesteps": view.T}

    dispatch = getattr(result, "operational", None) or getattr(result, "dispatch", None)
    network = getattr(result, "network", None)

    # capacity mix by technology (existing + built)
    mix: dict[str, float] = {}
    for g in view.gens:
        cap = g.p_nom_existing
        built = getattr(result, "built_capacity_mw", {}) or {}
        cap += built.get(g.id, 0.0)
        mix[g.tech] = mix.get(g.tech, 0.0) + cap
    s["capacity_mix_mw"] = {k: round(v, 1) for k, v in mix.items()}
    s["total_cost"] = round(getattr(result, "total_cost", 0.0)
                            or getattr(result, "objective", 0.0), 1)

    if dispatch:
        # VRE penetration, curtailment, unserved
        total_gen = sum(sum(v) for v in dispatch.generation_mw.values())
        vre_gen = sum(sum(dispatch.generation_mw.get(g.id, []))
                      for g in view.gens if g.is_vre)
        curtail = sum(sum(v) for v in dispatch.curtailment_mw.values())
        unserved = sum(sum(v) for v in dispatch.unserved_mw.values())
        s["vre_penetration"] = round(vre_gen / total_gen, 4) if total_gen else 0.0
        s["curtailment_mwh_weighted"] = round(curtail, 1)
        s["unserved_mwh_weighted"] = round(unserved, 1)
        s["realized_capacity_factor"] = {
            g.id: round(dispatch.realized_capacity_factor.get(g.id, 0.0), 4)
            for g in view.gens if g.is_vre}

    if network:
        prices = list(network.nodal_price.values())
        if prices:
            s["avg_price"] = round(float(np.mean(prices)), 2)
            s["price_spread"] = round(float(max(prices) - min(prices)), 2)
        s["congestion"] = {k: round(v, 2) for k, v in network.dual_values.items()}
        s["nodal_prices"] = {k: round(v, 2) for k, v in network.nodal_price.items()}

    return s
