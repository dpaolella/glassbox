"""PyPSA oracle for the economic dispatch core (PRD Sections 11.1, 11.2).

Builds a single-snapshot, copper-plate economic dispatch from the world (thermal
generators with marginal costs, VRE with an availability cap, one load) and
solves it both with the transparent linopy core and with PyPSA's LOPF, comparing
the objective and the per-generator dispatch. VRE enters as an availability cap,
never a scalar capacity factor (Section 6.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ...engines.economic_core import (
    EconomicView,
    EngineOptions,
    GenSpec,
    _gen_marginal_cost,
    build_dispatch_model,
    solve_model,
)
from ...schema import World

try:  # PyPSA is a dev/test-only dependency
    import pypsa
    HAVE_PYPSA = True
except Exception:  # pragma: no cover
    pypsa = None
    HAVE_PYPSA = False

HOURS_PER_YEAR = 8760


@dataclass
class DispatchData:
    gens: list[dict] = field(default_factory=list)   # id, p_nom, mc, p_max_pu
    load_mw: float = 0.0
    voll: float = 10000.0


def build_dispatch_data(world: World, hour: int, weather_year: int = 0) -> DispatchData:
    store = world.time_series_store
    abs_h = weather_year * HOURS_PER_YEAR + hour
    gens: list[dict] = []
    for g in world.generators:
        if not g.in_service or g.is_candidate or g.p_max_mw <= 0:
            continue
        mc, _ = _gen_marginal_cost(world, g)
        p_max_pu = 1.0
        if g.is_vre and g.availability_profile_id and g.availability_profile_id in store:
            p_max_pu = float(store.get(g.availability_profile_id)[abs_h])
        gens.append({"id": g.id, "p_nom": g.p_max_mw, "mc": mc, "p_max_pu": p_max_pu})
    for h in world.hydro_units:
        gens.append({"id": h.id, "p_nom": h.p_max_mw, "mc": 1.0, "p_max_pu": 1.0})

    load = 0.0
    for ld in world.loads:
        if ld.demand_profile_id and ld.demand_profile_id in store:
            load += float(store.get(ld.demand_profile_id)[abs_h])
    voll = max((ld.voll_per_mwh for ld in world.loads), default=10000.0)
    return DispatchData(gens=gens, load_mw=load, voll=voll)


def glassbox_copperplate_dispatch(data: DispatchData) -> dict:
    """Single-node, single-snapshot dispatch via the transparent linopy core."""
    specs = []
    for g in data.gens:
        specs.append(GenSpec(
            id=g["id"], node="sys", tech="x", is_vre=g["p_max_pu"] < 1.0,
            marginal_cost=g["mc"], emissions_t_per_mwh=0.0,
            p_nom_existing=g["p_nom"], is_candidate=False, capex_annual_per_mw=0.0,
            build_max=0.0, p_min_pu=0.0, ramp_per_h=g["p_nom"], min_up_h=0,
            min_down_h=0, start_cost=0.0, no_load_cost=0.0, reserve_eligible=False,
            availability=np.array([g["p_max_pu"]])))
    view = EconomicView(
        nodes=["sys"], T=1, period_ids=np.array([0]), period_weight=np.array([1.0]),
        annual_divisor=1.0, gens=specs, storages=[],
        load=np.array([[data.load_mw]]), lines=[], network_mode="transport",
        reference_node="sys", interfaces=[], reserve_req=np.array([0.0]),
        carbon_price=0.0, voll=data.voll)
    model = build_dispatch_model(view, EngineOptions(investment=False,
                                                     unit_commitment=False,
                                                     reserves=False, label="oracle"))
    solve_model(model)
    gp = model.m.variables["gen_p"].solution
    dispatch = {g["id"]: float(gp.sel(g=g["id"]).values[0]) for g in data.gens}
    return {"objective": float(model.m.objective.value), "dispatch": dispatch}


def pypsa_copperplate_dispatch(data: DispatchData) -> dict:
    """The same dispatch built and solved in PyPSA (the oracle)."""
    if not HAVE_PYPSA:
        raise RuntimeError("pypsa not available")
    n = pypsa.Network()
    n.set_snapshots([0])
    n.add("Bus", "sys")
    n.add("Load", "load", bus="sys", p_set=data.load_mw)
    for g in data.gens:
        n.add("Generator", g["id"], bus="sys", p_nom=g["p_nom"],
              marginal_cost=g["mc"], p_max_pu=g["p_max_pu"])
    # an unserved-energy generator at VOLL so the two models share the same
    # scarcity behavior
    n.add("Generator", "__unserved__", bus="sys", p_nom=data.load_mw * 2,
          marginal_cost=data.voll)
    n.optimize(solver_name="highs")
    p = n.generators_t.p.iloc[0]
    dispatch = {g["id"]: float(p.get(g["id"], 0.0)) for g in data.gens}
    return {"objective": float(n.objective), "dispatch": dispatch}


@dataclass
class DispatchComparison:
    objective_glassbox: float
    objective_pypsa: float
    objective_rel_diff: float
    max_dispatch_diff_mw: float
    total_dispatch_glassbox_mw: float
    total_dispatch_pypsa_mw: float


def compare_dispatch(world: World, hour: int, weather_year: int = 0) -> DispatchComparison:
    data = build_dispatch_data(world, hour, weather_year)
    gb = glassbox_copperplate_dispatch(data)
    px = pypsa_copperplate_dispatch(data)
    diffs = [abs(gb["dispatch"][g["id"]] - px["dispatch"][g["id"]]) for g in data.gens]
    obj_ref = max(abs(px["objective"]), 1.0)
    return DispatchComparison(
        objective_glassbox=gb["objective"],
        objective_pypsa=px["objective"],
        objective_rel_diff=abs(gb["objective"] - px["objective"]) / obj_ref,
        max_dispatch_diff_mw=float(max(diffs) if diffs else 0.0),
        total_dispatch_glassbox_mw=float(sum(gb["dispatch"].values())),
        total_dispatch_pypsa_mw=float(sum(px["dispatch"].values())),
    )
