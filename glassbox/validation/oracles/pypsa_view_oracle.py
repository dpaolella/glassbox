"""Deep PyPSA oracles for the economic core (issue #14, PRD Section 11).

The original ``pypsa_oracle`` compares a *single-hour, copper-plate* dispatch —
storage, hydro budgets, network limits and investment are never exercised. The
two comparisons here close that gap by translating the SAME resolved
``EconomicView`` the engines consume into a PyPSA network, so every constraint
family of the transparent linopy formulation is checked against an independent
implementation:

  * ``compare_dispatch_window`` — a multi-hour, zonal transport-network economic
    dispatch: storage SOC dynamics (cyclic), the real hydro reservoir energy
    budget, and inter-zonal transfer limits are all active on both sides.
  * ``compare_expansion`` — capacity expansion vs PyPSA ``p_nom_extendable``:
    candidate generators, supply-curve tranches and candidate transmission are
    extendable on both sides, the RPS enters with the exact same alternative
    compliance payment, and built MW per candidate + total cost must agree.

Honest scope notes (also surfaced by the API):

  * Both sides are built from the same ``assemble_view`` translation, so this
    validates the optimization layer (formulation + solver), not the
    world-to-view resolution — the single-hour oracle and the pandapower/Andes
    round-trips cover other layers.
  * Storage SOC bounds are normalized to [0, 1] on BOTH sides — PyPSA's
    StorageUnit has no soc_min; the normalization is applied identically so the
    compared problems stay the same.
  * Candidate *storage* is excluded from the expansion comparison (and reported):
    the transparent core sizes power and energy independently, while a PyPSA
    StorageUnit sizes p_nom at fixed max_hours — the problems would differ.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ...engines.economic_core import (
    RPS_ACP_PER_MWH,
    EconomicView,
    EngineOptions,
    assemble_view,
    build_dispatch_model,
    solve_model,
)
from ...operators.spatial import SpatialMode, SpatialProjection
from ...schema import World
from .pypsa_oracle import HAVE_PYPSA

if HAVE_PYPSA:
    import pandas as pd
    import pypsa

HOURS_PER_YEAR = 8760


# --- shared view construction -------------------------------------------------


def _zonal_window_view(world: World, start: int, hours: int, weather_year: int,
                       *, investment: bool) -> tuple[EconomicView, list[str]]:
    """One contiguous chronological window on the zonal (transport) network.

    A single period keeps the kernel's per-period constructs (cyclic SOC, hydro
    budget) exactly mirrorable in PyPSA, whose cyclic storage and ``e_sum_max``
    span the whole snapshot horizon. For expansion, hours are weighted up to a
    year (8760/T) so annualized capex competes with operations on the same
    footing as the real CEM.
    """
    notes: list[str] = []
    sview = SpatialProjection(SpatialMode.AGGREGATE).apply(world)
    abs_ts = weather_year * HOURS_PER_YEAR + np.arange(start, start + hours)
    period_ids = np.zeros(hours, dtype=int)
    weight = np.full(hours, HOURS_PER_YEAR / hours if investment else 1.0)
    view = assemble_view(world, sview, abs_ts, period_ids, weight, 1.0,
                         investment=investment)

    # normalizations applied to BOTH sides so the compared problems stay equal
    if any(s.soc_min_pu != 0.0 or s.soc_max_pu != 1.0 for s in view.storages):
        notes.append("storage SOC bounds normalized to [0,1] on both sides "
                     "(PyPSA StorageUnit has no soc_min)")
        for s in view.storages:
            s.soc_min_pu, s.soc_max_pu = 0.0, 1.0
    if view.emissions_cap is not None:
        notes.append("annual emissions cap dropped on both sides (not "
                     "translated); carbon price IS applied on both sides")
        view.emissions_cap = None
    return view, notes


# --- view -> PyPSA translation -------------------------------------------------


def pypsa_from_view(view: EconomicView) -> "pypsa.Network":
    """Rebuild an EconomicView as a PyPSA network, feature for feature.

    Candidate storages must be filtered out by the caller (see module docstring).
    """
    if not HAVE_PYPSA:
        raise RuntimeError("pypsa not available")
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(view.T))
    # objective weighting mirrors the kernel's w_t = period_weight/annual_divisor;
    # 'generators' and 'stores' weightings stay 1.0 because the kernel's hydro
    # budget and SOC recursion are unweighted too.
    w = view.period_weight / view.annual_divisor
    n.snapshot_weightings["objective"] = w

    for i, node in enumerate(view.nodes):
        n.add("Bus", node)
        n.add("Load", f"load_{node}", bus=node,
              p_set=pd.Series(view.load[i], index=n.snapshots))
        # unserved energy at VOLL, per node (the kernel's `unserved` variable)
        n.add("Generator", f"__unserved_{node}", bus=node,
              p_nom=float(view.load[i].max()) * 1.05 + 1.0,
              marginal_cost=view.voll)

    for g in view.gens:
        kw: dict = dict(bus=g.node,
                        marginal_cost=g.marginal_cost
                        + view.carbon_price * g.emissions_t_per_mwh)
        if g.availability is not None:
            kw["p_max_pu"] = pd.Series(np.clip(g.availability, 0.0, None),
                                       index=n.snapshots)
        if g.energy_limit_per_period is not None:
            # single-period views only: the kernel's per-period hydro budget
            # equals PyPSA's whole-horizon e_sum_max (weighting 'generators'=1)
            kw["e_sum_max"] = float(g.energy_limit_per_period)
        if g.is_candidate:
            kw.update(p_nom=0.0, p_nom_extendable=True,
                      p_nom_max=float(g.build_max),
                      capital_cost=float(g.capex_annual_per_mw))
        else:
            kw["p_nom"] = float(g.p_nom_existing)
        n.add("Generator", g.id, **kw)

    for s in view.storages:
        if s.is_candidate:
            raise ValueError(f"candidate storage {s.id} must be excluded before "
                             "translation (P/E sized independently in the kernel)")
        if s.p_nom_existing <= 0 or s.e_nom_existing <= 0:
            continue
        n.add("StorageUnit", s.id, bus=s.node,
              p_nom=float(s.p_nom_existing),
              max_hours=float(s.e_nom_existing / s.p_nom_existing),
              efficiency_store=float(s.eff_c),
              efficiency_dispatch=float(s.eff_d),
              marginal_cost=float(s.vom),
              cyclic_state_of_charge=True)

    # zonal corridors (and candidate corridors) are lossless bidirectional
    # transport, exactly the kernel's transport-mode flow variable
    for ln in view.lines:
        if ln.is_candidate:
            n.add("Link", ln.id, bus0=ln.a, bus1=ln.b, p_min_pu=-1.0,
                  p_nom=0.0, p_nom_extendable=True,
                  p_nom_max=float(ln.build_max),
                  capital_cost=float(ln.capex_annual_per_mw))
        else:
            n.add("Link", ln.id, bus0=ln.a, bus1=ln.b, p_min_pu=-1.0,
                  p_nom=float(ln.rating))
    return n


def _rps_extra_functionality(view: EconomicView):
    """Mirror the kernel's RPS + alternative-compliance-payment block inside
    PyPSA's linopy model (the exact production formulation, same ACP)."""
    w = view.period_weight / view.annual_divisor
    vre_ids = [g.id for g in view.gens if g.is_vre]
    target = view.rps_fraction * float(
        (view.load.sum(axis=0) * view.period_weight).sum() / view.annual_divisor)

    def extra(n, snapshots):
        m = n.model
        # PyPSA 1.x names the component axis "name" (older versions "Generator")
        gen_dim = ("name" if "name" in m.variables["Generator-p"].dims
                   else "Generator")
        p = m.variables["Generator-p"].sel({gen_dim: vre_ids})
        import xarray as xr
        wda = xr.DataArray(w, coords={"snapshot": snapshots}, dims=["snapshot"])
        short = m.add_variables(lower=0.0, name="rps_shortfall")
        m.add_constraints((p * wda).sum() + short >= target, name="rps")
        m.objective += short * RPS_ACP_PER_MWH

    return extra if vre_ids and view.rps_fraction > 0 else None


def _optimize(n: "pypsa.Network", extra=None) -> None:
    # no non-extendable asset carries a capital cost here, so the objective
    # constant is zero and n.objective is directly comparable to the kernel's
    status, condition = n.optimize(solver_name="highs",
                                   extra_functionality=extra,
                                   include_objective_constant=False,
                                   output_flag=False)
    if status != "ok":
        raise RuntimeError(f"pypsa solve failed: {status}/{condition}")


# --- comparison 1: multi-hour zonal dispatch window ----------------------------


@dataclass
class WindowComparison:
    start_hour: int
    hours: int
    n_nodes: int
    objective_glassbox: float
    objective_pypsa: float
    objective_rel_diff: float
    max_gen_energy_diff_mwh: float
    storage_throughput_glassbox_mwh: float
    storage_throughput_pypsa_mwh: float
    hydro_energy_glassbox_mwh: float
    hydro_energy_pypsa_mwh: float
    hydro_budget_mwh: float | None
    unserved_glassbox_mwh: float
    unserved_pypsa_mwh: float
    total_load_energy_mwh: float
    corridor_congested_hours: int
    notes: list[str] = field(default_factory=list)


def compare_dispatch_window(world: World, start: int = 0, hours: int = 168,
                            weather_year: int = 0) -> WindowComparison:
    """Zonal multi-hour economic dispatch, kernel vs PyPSA.

    Unlike the single-hour oracle, this window exercises cyclic storage SOC,
    the hydro reservoir energy budget, and inter-zonal transfer limits.
    """
    view, notes = _zonal_window_view(world, start, hours, weather_year,
                                     investment=False)

    # kernel side
    built = build_dispatch_model(view, EngineOptions(
        investment=False, unit_commitment=False, reserves=False,
        label="oracle-window"))
    status = solve_model(built)
    if "ok" not in status and "optimal" not in status.lower():
        raise RuntimeError(f"kernel solve failed: {status}")
    m = built.m
    gp = m.variables["gen_p"].solution
    gb_energy = {g.id: float(gp.sel(g=g.id).values.sum()) for g in view.gens}
    gb_uns = float(m.variables["unserved"].solution.values.sum())
    gb_thru = 0.0
    if view.storages:
        gb_thru = float(m.variables["sto_discharge"].solution.values.sum())
    congested = 0
    if view.lines and "flow" in m.variables:
        fl = m.variables["flow"].solution
        for ln in view.lines:
            series = np.abs(fl.sel(l=ln.id).values)
            congested += int((series >= ln.rating - 1e-3).sum())

    # oracle side
    n = pypsa_from_view(view)
    _optimize(n)
    p = n.generators_t.p
    px_energy = {g.id: float(p[g.id].sum()) if g.id in p.columns else 0.0
                 for g in view.gens}
    px_uns = float(sum(p[c].sum() for c in p.columns if c.startswith("__unserved_")))
    px_thru = 0.0
    if len(n.storage_units):
        px_thru = float(n.storage_units_t.p_dispatch.values.sum())

    hydro_ids = [g.id for g in view.gens if g.energy_limit_per_period is not None]
    budget = None
    if hydro_ids:
        budget = float(sum(g.energy_limit_per_period for g in view.gens
                           if g.energy_limit_per_period is not None))
    obj_ref = max(abs(float(n.objective)), 1.0)
    return WindowComparison(
        start_hour=start, hours=hours, n_nodes=len(view.nodes),
        objective_glassbox=float(m.objective.value),
        objective_pypsa=float(n.objective),
        objective_rel_diff=abs(float(m.objective.value) - float(n.objective)) / obj_ref,
        max_gen_energy_diff_mwh=float(max(
            (abs(gb_energy[gid] - px_energy[gid]) for gid in gb_energy),
            default=0.0)),
        storage_throughput_glassbox_mwh=gb_thru,
        storage_throughput_pypsa_mwh=px_thru,
        hydro_energy_glassbox_mwh=float(sum(gb_energy[h] for h in hydro_ids)),
        hydro_energy_pypsa_mwh=float(sum(px_energy[h] for h in hydro_ids)),
        hydro_budget_mwh=budget,
        unserved_glassbox_mwh=gb_uns,
        unserved_pypsa_mwh=px_uns,
        total_load_energy_mwh=float(view.load.sum()),
        corridor_congested_hours=congested,
        notes=notes,
    )


# --- comparison 2: capacity expansion vs p_nom_extendable ----------------------


@dataclass
class ExpansionComparison:
    hours: int
    rps_fraction: float
    objective_glassbox: float
    objective_pypsa: float
    objective_rel_diff: float
    built_glassbox_mw: dict[str, float]
    built_pypsa_mw: dict[str, float]
    max_build_diff_mw: float
    total_built_glassbox_mw: float
    total_built_pypsa_mw: float
    excluded_candidate_storage: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def compare_expansion(world: World, start: int = 0, hours: int = 168,
                      weather_year: int = 0) -> ExpansionComparison:
    """Capacity expansion, kernel vs PyPSA ``p_nom_extendable``.

    Candidate generators, supply-curve tranches and candidate transmission are
    all investment decisions on both sides; the RPS enters with the identical
    alternative-compliance-payment formulation. Built MW per candidate and the
    total (capex + operations) cost must agree.
    """
    view, notes = _zonal_window_view(world, start, hours, weather_year,
                                     investment=True)

    # candidate storage sizes P and E independently in the kernel — a PyPSA
    # StorageUnit cannot; exclude it from BOTH sides and say so.
    excluded_sto = [s.id for s in view.storages if s.is_candidate]
    if excluded_sto:
        view.storages = [s for s in view.storages if not s.is_candidate]
        notes.append("candidate storage excluded on both sides (kernel sizes "
                     "power and energy independently; PyPSA StorageUnit cannot)")

    # kernel side (reserves off on both sides: PyPSA has no reserve product)
    built = build_dispatch_model(view, EngineOptions(
        investment=True, unit_commitment=False, reserves=False,
        label="oracle-cem"))
    status = solve_model(built)
    if "ok" not in status and "optimal" not in status.lower():
        raise RuntimeError(f"kernel solve failed: {status}")
    m = built.m
    gb_built: dict[str, float] = {}
    if "gen_build" in m.variables:
        sol = m.variables["gen_build"].solution
        for g in view.gens:
            if g.is_candidate:
                gb_built[g.id] = float(sol.sel(g=g.id))
    if "line_build" in m.variables:
        sol = m.variables["line_build"].solution
        for ln in view.lines:
            if ln.is_candidate:
                gb_built[ln.id] = float(sol.sel(l=ln.id))

    # oracle side
    n = pypsa_from_view(view)
    _optimize(n, extra=_rps_extra_functionality(view))
    px_built: dict[str, float] = {}
    ext_g = n.generators[n.generators.p_nom_extendable]
    for gid, row in ext_g.iterrows():
        px_built[str(gid)] = float(row.p_nom_opt)
    ext_l = n.links[n.links.p_nom_extendable]
    for lid, row in ext_l.iterrows():
        px_built[str(lid)] = float(row.p_nom_opt)

    all_ids = sorted(set(gb_built) | set(px_built))
    max_diff = float(max((abs(gb_built.get(i, 0.0) - px_built.get(i, 0.0))
                          for i in all_ids), default=0.0))
    obj_ref = max(abs(float(n.objective)), 1.0)
    return ExpansionComparison(
        hours=hours, rps_fraction=view.rps_fraction,
        objective_glassbox=float(m.objective.value),
        objective_pypsa=float(n.objective),
        objective_rel_diff=abs(float(m.objective.value) - float(n.objective)) / obj_ref,
        built_glassbox_mw={k: round(v, 2) for k, v in gb_built.items() if v > 1e-3},
        built_pypsa_mw={k: round(v, 2) for k, v in px_built.items() if v > 1e-3},
        max_build_diff_mw=max_diff,
        total_built_glassbox_mw=float(sum(gb_built.values())),
        total_built_pypsa_mw=float(sum(px_built.values())),
        excluded_candidate_storage=excluded_sto,
        notes=notes,
    )
