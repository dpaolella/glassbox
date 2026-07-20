"""Shared transparent economic dispatch/investment formulation (PRD 6.2, 6.3).

Both CEM and PCM build on one explicit linopy formulation so explain() can
surface the objective, constraints and duals (Section 2.3 / 6.1). The differences
between the two engines are expressed as options, not as different libraries:

  * CEM: investment on, unit commitment off, representative periods with
    per-period cyclic storage; objective annualizes capex + weighted operations.
  * PCM: investment off (capacities fixed), unit commitment on (binaries, min
    up/down, startup), full chronology with ramping and chronological storage.

The locational marginal price is the dual of the per-node power-balance
constraint; for the MILP (PCM) we fix the commitment and re-solve the LP to read
prices, a standard and teachable technique.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import linopy
import numpy as np
import pandas as pd
import xarray as xr

from ..operators.spatial import SpatialView
from ..schema import World


# RPS/CES alternative compliance payment ($/MWh short). Module-level so the
# PyPSA expansion oracle can mirror the exact same policy term (issue #14).
RPS_ACP_PER_MWH = 150.0


# --- finance ----------------------------------------------------------------


def capital_recovery_factor(rate: float, lifetime_yr: int) -> float:
    """CRF = r(1+r)^n / ((1+r)^n - 1); annuitizes an overnight capex."""
    if rate <= 0:
        return 1.0 / lifetime_yr
    f = (1 + rate) ** lifetime_yr
    return rate * f / (f - 1)


# --- resolved view ----------------------------------------------------------


@dataclass
class GenSpec:
    id: str
    node: str
    tech: str
    is_vre: bool
    marginal_cost: float          # $/MWh (fuel*heat_rate + vom)
    emissions_t_per_mwh: float    # tCO2/MWh
    p_nom_existing: float          # MW already built
    is_candidate: bool
    capex_annual_per_mw: float     # annuitized capex + FOM, $/MW/yr
    build_max: float
    p_min_pu: float
    ramp_per_h: float              # MW/h (fraction of pmax * 60)
    min_up_h: int
    min_down_h: int
    start_cost: float
    no_load_cost: float
    reserve_eligible: bool
    availability: Optional[np.ndarray] = None   # len T, VRE only
    energy_limit_per_period: Optional[float] = None  # hydro reservoir budget
    parent_id: Optional[str] = None  # set on supply-curve tranches: the zonal
                                     # ResourcePotential this tranche belongs to


@dataclass
class StorageSpec:
    id: str
    node: str
    p_nom_existing: float          # MW existing discharge power
    e_nom_existing: float          # MWh existing energy
    is_candidate: bool
    capex_annual_per_mw: float
    capex_annual_per_mwh: float
    eff_c: float
    eff_d: float
    soc_min_pu: float
    soc_max_pu: float
    vom: float
    build_max_mw: float
    build_max_mwh: float
    parent_id: Optional[str] = None  # set on supply-curve tranches


@dataclass
class LineSpec:
    id: str
    a: str        # from node
    b: str        # to node
    x: float      # reactance (pu)
    rating: float  # MW (existing capacity, or the build ceiling for a candidate)
    is_candidate: bool = False
    capex_annual_per_mw: float = 0.0
    build_max: float = 0.0
    transport_only: bool = False  # candidate additions are modeled as transport
                                  # corridors (standard CEM simplification), so
                                  # they carry no DC angle coupling


@dataclass
class EconomicView:
    nodes: list[str]
    T: int
    period_ids: np.ndarray          # len T, which representative period each t is in
    period_weight: np.ndarray        # len T, hours represented by each t
    annual_divisor: float            # n weather years (annualizes the op cost)
    gens: list[GenSpec]
    storages: list[StorageSpec]
    load: np.ndarray                 # [n_nodes, T], MW
    lines: list[LineSpec]            # nodal DC branches OR zonal corridors
    network_mode: str                # 'dc' | 'transport'
    reference_node: str
    interfaces: list[dict] = field(default_factory=list)
    reserve_req: Optional[np.ndarray] = None   # len T, MW spinning requirement
    reserve_pct_vre: float = 0.0               # candidate-VRE reserve rule (endogenous)
    carbon_price: float = 0.0
    emissions_cap: Optional[float] = None       # annual tCO2
    rps_fraction: float = 0.0
    voll: float = 10000.0

    def node_index(self) -> dict[str, int]:
        return {n: i for i, n in enumerate(self.nodes)}


@dataclass
class EngineOptions:
    investment: bool = False
    unit_commitment: bool = False
    reserves: bool = True
    label: str = "economic"
    # when set (gid -> array of 0/1 over T), commitment is fixed and the model
    # becomes a pure LP so power-balance duals (LMPs) are available.
    fixed_commitment: Optional[dict[str, np.ndarray]] = None
    # rolling-horizon storage feedforward (rtops RT-SCED): initial SOC as a
    # fraction of energy capacity per storage id. When set, SOC dynamics are
    # NON-cyclic with duration-weighted energy (a 5-min step moves 1/12 the
    # energy of an hour) — a short window can then net-discharge what an
    # earlier horizon stored, instead of being forced back to its start SOC.
    # None (default) keeps the existing cyclic per-period formulation, so
    # every planning result is untouched. Not valid with investment=True.
    storage_soc_init: Optional[dict[str, float]] = None
    # ORDC-lite (rtops): a stepped reserve demand curve as (fraction_of_req,
    # price) tranches, e.g. [(0.5, 300), (1.0, 2000)] — the first half of any
    # shortage priced at $300/MWh, the rest at $2000. Prices flow into the
    # energy LMP through the coupled constraint, so scarcity shows up in
    # prices BEFORE load is shed. None (default) keeps the single
    # 0.5 x VOLL shortfall price: planning untouched.
    reserve_curve: Optional[list[tuple[float, float]]] = None


# --- assembler --------------------------------------------------------------


def _gen_marginal_cost(world: World, g) -> tuple[float, float]:
    """Return (marginal_cost $/MWh, emissions tCO2/MWh) for a generator."""
    if g.is_vre:
        return g.vom_per_mwh, 0.0
    hr = g.heat_rate_mmbtu_per_mwh or 0.0
    fuel_price = 0.0
    emis = 0.0
    if g.fuel_id:
        fuel = next((f for f in world.fuels if f.id == g.fuel_id), None)
        if fuel:
            fuel_price = fuel.price_per_mmbtu
            emis = fuel.emissions_tco2_per_mmbtu
    return hr * fuel_price + g.vom_per_mwh, hr * emis


def _candidate_marginal_cost(world: World, c) -> float:
    """Marginal operating cost ($/MWh) of a candidate generator."""
    if c.technology in ("wind", "solar_pv"):
        return c.vom_per_mwh
    hr = c.heat_rate_mmbtu_per_mwh or 0.0
    fuel = next((f for f in world.fuels if f.id == c.fuel_id), None)
    return hr * (fuel.price_per_mmbtu if fuel else 0.0) + c.vom_per_mwh


def _candidate_emissions(world: World, c) -> float:
    """Emissions (tCO2/MWh) of a candidate generator."""
    if c.technology in ("wind", "solar_pv"):
        return 0.0
    hr = c.heat_rate_mmbtu_per_mwh or 0.0
    fuel = next((f for f in world.fuels if f.id == c.fuel_id), None)
    return hr * (fuel.emissions_tco2_per_mmbtu if fuel else 0.0)


def _zone_hub_bus(world: World, rp) -> Optional[str]:
    """The bus a zonal resource potential interconnects at: its explicit
    ``bus_id`` if given, else the zone's first member bus (its hub)."""
    if rp.bus_id:
        return rp.bus_id
    zone = next((z for z in world.zones if z.id == rp.zone_id), None)
    if zone and zone.member_bus_ids:
        return zone.member_bus_ids[0]
    return None


def assemble_view(
    world: World,
    spatial_view: SpatialView,
    abs_timesteps: np.ndarray,
    period_ids: np.ndarray,
    period_weight: np.ndarray,
    annual_divisor: float,
    *,
    investment: bool,
    discount_rate: float = 0.07,
) -> EconomicView:
    """Resolve the world + projections into a flat numeric EconomicView."""
    store = world.time_series_store
    bus_to_node = spatial_view.bus_to_node
    nodes = list(spatial_view.node_ids)
    node_idx = {n: i for i, n in enumerate(nodes)}
    T = len(abs_timesteps)

    # --- load per node ---
    load = np.zeros((len(nodes), T))
    for ld in world.loads:
        node = bus_to_node.get(ld.bus_id)
        if node is None or ld.demand_profile_id is None:
            continue
        if ld.demand_profile_id not in store:
            continue
        series = store.get(ld.demand_profile_id)[abs_timesteps]
        load[node_idx[node]] += series * world.demand_scale

    # --- existing generators (physical assets; capacity fixed) ---
    gens: list[GenSpec] = []
    for g in world.generators:
        # retired / out-of-service assets are invisible to the economic layers
        if not g.in_service or g.status.value == "retired":
            continue
        node = bus_to_node.get(g.bus_id)
        if node is None:
            continue
        mc, emis = _gen_marginal_cost(world, g)
        avail = None
        if g.is_vre and g.availability_profile_id and g.availability_profile_id in store:
            avail = store.get(g.availability_profile_id)[abs_timesteps]
        ramp = (g.ramp_up_mw_per_min or g.p_max_mw / 60.0) * 60.0  # MW/h
        gens.append(GenSpec(
            id=g.id, node=node, tech=g.technology.value, is_vre=g.is_vre,
            marginal_cost=mc, emissions_t_per_mwh=emis,
            p_nom_existing=g.p_max_mw, is_candidate=False,
            capex_annual_per_mw=0.0, build_max=0.0,
            p_min_pu=(0.0 if g.is_vre else g.p_min_pu),
            ramp_per_h=ramp,
            min_up_h=int(round(g.min_up_time_h)), min_down_h=int(round(g.min_down_time_h)),
            start_cost=g.start_cost, no_load_cost=g.no_load_cost,
            reserve_eligible=bool(g.reserve_eligible) and not g.is_vre,
            availability=avail))

    # --- candidate generators (investment options; only built by CEM) ---
    if investment:
        for c in world.expansion_candidates:
            if c.kind.value != "generator":
                continue
            node = bus_to_node.get(c.bus_id)
            if node is None:
                continue
            is_vre = c.technology in ("wind", "solar_pv")
            mc = _candidate_marginal_cost(world, c)
            emis = _candidate_emissions(world, c)
            avail = None
            if is_vre and c.availability_profile_id and c.availability_profile_id in store:
                avail = store.get(c.availability_profile_id)[abs_timesteps]
            crf = capital_recovery_factor(discount_rate, c.lifetime_yr)
            capex_annual = (c.capex_per_mw or 0.0) * crf + c.fom_per_mw_yr
            gens.append(GenSpec(
                id=c.id, node=node, tech=c.technology, is_vre=is_vre,
                marginal_cost=mc, emissions_t_per_mwh=emis,
                p_nom_existing=0.0, is_candidate=True,
                capex_annual_per_mw=capex_annual,
                build_max=(c.build_max_mw or 0.0),
                p_min_pu=(0.0 if is_vre else c.p_min_pu),
                ramp_per_h=(c.build_max_mw or 0.0),
                min_up_h=0, min_down_h=0, start_cost=0.0, no_load_cost=0.0,
                reserve_eligible=not is_vre, availability=avail))

        # --- zonal resource potential: expand each supply curve into one
        # candidate gen per tranche (cheapest first), sited at the zone hub ---
        for rp in world.resource_potentials:
            if rp.kind.value != "generator":
                continue
            hub_node = bus_to_node.get(_zone_hub_bus(world, rp))
            if hub_node is None:
                continue
            is_vre = rp.technology in ("wind", "solar_pv")
            mc = _candidate_marginal_cost(world, rp)
            emis = _candidate_emissions(world, rp)
            crf = capital_recovery_factor(discount_rate, rp.lifetime_yr)
            for k, tr in enumerate(rp.tranches):
                fom = tr.fom_per_mw_yr if tr.fom_per_mw_yr is not None else rp.fom_per_mw_yr
                capex_annual = tr.capex_per_mw * crf + fom
                prof_id = tr.availability_profile_id or rp.availability_profile_id
                avail = (store.get(prof_id)[abs_timesteps]
                         if is_vre and prof_id and prof_id in store else None)
                # each step can interconnect at its own bus (spread across the
                # zone instead of concentrating the whole curve at one hub)
                tr_node = (bus_to_node.get(tr.bus_id) if tr.bus_id else None) or hub_node
                gens.append(GenSpec(
                    id=f"{rp.id}#t{k}", node=tr_node, tech=rp.technology, is_vre=is_vre,
                    marginal_cost=mc, emissions_t_per_mwh=emis,
                    p_nom_existing=0.0, is_candidate=True,
                    capex_annual_per_mw=capex_annual, build_max=tr.build_max_mw,
                    p_min_pu=(0.0 if is_vre else rp.p_min_pu),
                    ramp_per_h=tr.build_max_mw, min_up_h=0, min_down_h=0,
                    start_cost=0.0, no_load_cost=0.0,
                    reserve_eligible=not is_vre, availability=avail, parent_id=rp.id))

    # --- hydro as energy-limited dispatchable gens ---
    for h in world.hydro_units:
        if not h.in_service:
            continue
        node = bus_to_node.get(h.bus_id)
        if node is None:
            continue
        # energy budget per representative period (scaled by period length)
        n_periods = len(np.unique(period_ids))
        budget = h.reservoir_energy_mwh if h.reservoir_energy_mwh > 0 else None
        gens.append(GenSpec(
            id=h.id, node=node, tech="hydro", is_vre=False,
            marginal_cost=1.0, emissions_t_per_mwh=0.0,
            p_nom_existing=h.p_max_mw, is_candidate=False,
            capex_annual_per_mw=0.0, build_max=0.0, p_min_pu=0.0,
            ramp_per_h=h.p_max_mw, min_up_h=0, min_down_h=0,
            start_cost=0.0, no_load_cost=0.0, reserve_eligible=True,
            energy_limit_per_period=budget))

    # --- existing storage (fixed) + candidate storage (built by CEM) ---
    storages: list[StorageSpec] = []
    for s in world.storage_units:
        if not s.in_service:
            continue
        node = bus_to_node.get(s.bus_id)
        if node is None:
            continue
        storages.append(StorageSpec(
            id=s.id, node=node,
            p_nom_existing=s.p_discharge_max_mw, e_nom_existing=s.energy_capacity_mwh,
            is_candidate=False, capex_annual_per_mw=0.0, capex_annual_per_mwh=0.0,
            eff_c=s.efficiency_charge, eff_d=s.efficiency_discharge,
            soc_min_pu=s.soc_min_pu, soc_max_pu=s.soc_max_pu, vom=s.vom_per_mwh,
            build_max_mw=0.0, build_max_mwh=0.0))
    if investment:
        for c in world.expansion_candidates:
            if c.kind.value != "storage":
                continue
            node = bus_to_node.get(c.bus_id)
            if node is None:
                continue
            crf = capital_recovery_factor(discount_rate, c.lifetime_yr)
            capex_p = (c.capex_per_mw or 0.0) * crf + c.fom_per_mw_yr
            capex_e = (c.capex_per_mwh or 0.0) * crf
            dur = c.duration_h or 4.0
            storages.append(StorageSpec(
                id=c.id, node=node, p_nom_existing=0.0, e_nom_existing=0.0,
                is_candidate=True, capex_annual_per_mw=capex_p,
                capex_annual_per_mwh=capex_e,
                eff_c=(0.95 if c.efficiency_charge is None else c.efficiency_charge),
                eff_d=(0.95 if c.efficiency_discharge is None else c.efficiency_discharge),
                soc_min_pu=0.05, soc_max_pu=1.0,
                vom=c.vom_per_mwh, build_max_mw=(c.build_max_mw or 0.0),
                build_max_mwh=(c.build_max_mw or 0.0) * dur))

        # zonal storage resource potential (supply curve of buildable storage)
        for rp in world.resource_potentials:
            if rp.kind.value != "storage":
                continue
            node = bus_to_node.get(_zone_hub_bus(world, rp))
            if node is None:
                continue
            crf = capital_recovery_factor(discount_rate, rp.lifetime_yr)
            dur = rp.duration_h or 4.0
            for k, tr in enumerate(rp.tranches):
                fom = tr.fom_per_mw_yr if tr.fom_per_mw_yr is not None else rp.fom_per_mw_yr
                capex_p = tr.capex_per_mw * crf + fom
                capex_e = (tr.capex_per_mwh if tr.capex_per_mwh is not None
                           else (rp.capex_per_mwh or 0.0)) * crf
                storages.append(StorageSpec(
                    id=f"{rp.id}#t{k}", node=node, p_nom_existing=0.0, e_nom_existing=0.0,
                    is_candidate=True, capex_annual_per_mw=capex_p,
                    capex_annual_per_mwh=capex_e,
                    eff_c=(0.95 if rp.efficiency_charge is None else rp.efficiency_charge),
                    eff_d=(0.95 if rp.efficiency_discharge is None else rp.efficiency_discharge),
                    soc_min_pu=0.05, soc_max_pu=1.0,
                    vom=rp.vom_per_mwh, build_max_mw=tr.build_max_mw,
                    build_max_mwh=tr.build_max_mw * dur, parent_id=rp.id))

    # --- transmission ---
    lines: list[LineSpec] = []
    if spatial_view.mode.value == "aggregate":
        network_mode = "transport"
        for (a, b), lim in spatial_view.transfer_limits_mw.items():
            lines.append(LineSpec(id=f"{a}__{b}", a=a, b=b, x=0.0, rating=lim))
    else:
        network_mode = "dc"
        for ln in world.ac_lines:
            if not ln.in_service:
                continue
            a = bus_to_node.get(ln.from_bus_id)
            b = bus_to_node.get(ln.to_bus_id)
            if a is None or b is None or a == b:
                continue
            lines.append(LineSpec(id=ln.id, a=a, b=b, x=max(ln.x, 1e-4),
                                  rating=ln.rating_normal_mva))
        for tr in world.transformers:
            a = bus_to_node.get(tr.from_bus_id)
            b = bus_to_node.get(tr.to_bus_id)
            if a is None or b is None or a == b:
                continue
            lines.append(LineSpec(id=tr.id, a=a, b=b, x=max(tr.x, 1e-4),
                                  rating=tr.rating_mva))

    # candidate transmission (a real CEM build decision). Modeled as a transport
    # corridor with a built-capacity variable — the standard CEM simplification
    # (the DC angle coupling of a not-yet-built line is nonconvex).
    if investment:
        for c in world.expansion_candidates:
            if c.kind.value != "line":
                continue
            a = bus_to_node.get(c.from_bus_id)
            b = bus_to_node.get(c.to_bus_id)
            if a is None or b is None or a == b:
                continue
            crf = capital_recovery_factor(discount_rate, c.lifetime_yr)
            capex_annual = (c.capex_per_mw or 0.0) * crf + c.fom_per_mw_yr
            ceiling = c.build_max_mw or 0.0
            lines.append(LineSpec(id=c.id, a=a, b=b, x=max(c.reactance_pu or 0.1, 1e-4),
                                  rating=ceiling, is_candidate=True,
                                  capex_annual_per_mw=capex_annual, build_max=ceiling,
                                  transport_only=True))

    # --- interfaces (only meaningful when lines are individually modeled) ---
    interfaces = []
    line_ids = {ln.id for ln in lines}
    line_by_id = {ln.id: ln for ln in lines}
    for iface in world.interfaces:
        members = [m for m in iface.member_line_ids if m in line_ids]
        if members and iface.limit_mw < 1e8:
            # a candidate line whose endpoints span the same two sides is a
            # parallel corridor: its flow counts toward the interface and its
            # built capacity legitimately expands the limit (issue #8)
            a_side = {line_by_id[m].a for m in members}
            b_side = {line_by_id[m].b for m in members}
            parallel = [ln.id for ln in lines if ln.is_candidate and
                        ((ln.a in a_side and ln.b in b_side)
                         or (ln.a in b_side and ln.b in a_side))]
            interfaces.append({"id": iface.id, "members": members,
                               "limit": iface.limit_mw,
                               "parallel_candidates": parallel})

    # --- reserves (spinning requirement) ---
    reserve_req = np.zeros(T)
    total_load_t = load.sum(axis=0)
    # Existing VRE contributes a precomputed requirement; *candidate* VRE
    # enters the reserve constraint endogenously via its build variable
    # (sizing on build_max would over-procure reserves for unbuilt options).
    vre_cap_avail_t = np.zeros(T)
    for g in gens:
        if g.availability is not None and g.p_nom_existing > 0:
            vre_cap_avail_t += g.availability * g.p_nom_existing
    reserve_pct_vre = 0.0
    for rp in world.reserve_products:
        if rp.kind.value in ("spinning",):
            rule = rp.requirement_rule
            reserve_req += rule.get("pct_load", 0.0) * total_load_t
            reserve_req += rule.get("pct_vre", 0.0) * vre_cap_avail_t
            reserve_req += rule.get("fixed_mw", 0.0)
            reserve_pct_vre = max(reserve_pct_vre, rule.get("pct_vre", 0.0))

    # --- policies ---
    carbon_price = 0.0
    emissions_cap = None
    rps_fraction = 0.0
    for pol in world.policies:
        if pol.kind.value == "carbon_price":
            carbon_price = pol.value
        elif pol.kind.value == "emissions_cap" and pol.value > 0:
            emissions_cap = pol.value
        elif pol.kind.value in ("rps", "ces"):
            rps_fraction = max(rps_fraction, pol.value)

    voll = max((ld.voll_per_mwh for ld in world.loads), default=10000.0)

    return EconomicView(
        nodes=nodes, T=T, period_ids=period_ids, period_weight=period_weight,
        annual_divisor=annual_divisor, gens=gens, storages=storages, load=load,
        lines=lines, network_mode=network_mode,
        reference_node=bus_to_node.get(world.reference_bus_id, nodes[0]),
        interfaces=interfaces, reserve_req=reserve_req,
        reserve_pct_vre=reserve_pct_vre, carbon_price=carbon_price,
        emissions_cap=emissions_cap, rps_fraction=rps_fraction, voll=voll)


# --- linopy model builder ---------------------------------------------------


@dataclass
class BuiltModel:
    m: linopy.Model
    view: EconomicView
    options: EngineOptions
    gen_ids: list[str]
    sto_ids: list[str]
    line_ids: list[str]
    meta: dict[str, Any] = field(default_factory=dict)


def build_dispatch_model(view: EconomicView, options: EngineOptions) -> BuiltModel:
    """Construct the explicit linopy formulation shared by CEM and PCM."""
    m = linopy.Model()
    T = view.T
    nidx = view.node_index()
    t_idx = pd.RangeIndex(T, name="t")

    gens = view.gens
    gen_ids = [g.id for g in gens]
    g_idx = pd.Index(gen_ids, name="g")
    disp_ids = [g.id for g in gens]

    # weights as xarray over t
    w = xr.DataArray(view.period_weight, coords=[t_idx], dims=["t"]) / view.annual_divisor

    # --- generator dispatch ---
    p = m.add_variables(lower=0.0, coords=[g_idx, t_idx], name="gen_p")

    # capacity: existing (param) + build (var, candidates only)
    cap_existing = xr.DataArray([g.p_nom_existing for g in gens], coords=[g_idx], dims=["g"])
    if options.investment:
        build_ub = xr.DataArray(
            [g.build_max if g.is_candidate else 0.0 for g in gens],
            coords=[g_idx], dims=["g"])
        gen_build = m.add_variables(lower=0.0, upper=build_ub, coords=[g_idx], name="gen_build")
        cap = cap_existing + gen_build
    else:
        gen_build = None
        cap = cap_existing

    # availability matrix for VRE (1.0 for dispatchable)
    avail = np.ones((len(gens), T))
    for i, g in enumerate(gens):
        if g.availability is not None:
            avail[i] = g.availability
    avail_da = xr.DataArray(avail, coords=[g_idx, t_idx], dims=["g", "t"])

    # commitment (PCM only). Cyclable thermal units carry binaries; units with
    # very long min-up times (e.g. nuclear) are treated as must-run instead, to
    # keep the MILP tractable — a standard and realistic simplification.
    uc_ids = []
    mustrun_ids = []
    if options.unit_commitment:
        for g in gens:
            if g.is_vre or g.energy_limit_per_period is not None or g.p_min_pu <= 0:
                continue
            if g.tech == "nuclear" or g.min_up_h > 12:
                mustrun_ids.append(g.id)
            else:
                uc_ids.append(g.id)
    fixed = options.fixed_commitment
    binary_uc = bool(uc_ids) and fixed is None
    if binary_uc:
        u_idx = pd.Index(uc_ids, name="g")
        u = m.add_variables(coords=[u_idx, t_idx], binary=True, name="commit")
        su = m.add_variables(lower=0.0, upper=1.0, coords=[u_idx, t_idx], name="startup")
        sd = m.add_variables(lower=0.0, upper=1.0, coords=[u_idx, t_idx], name="shutdown")
    else:
        u = su = sd = None

    # upper bound on dispatch: cap * availability (and <= u*cap for committed)
    m.add_constraints(p <= cap * avail_da, name="gen_pmax")
    if binary_uc:
        for g in gens:
            if g.id in uc_ids:
                gp = p.sel(g=g.id)
                uu = u.sel(g=g.id)
                pmax = g.p_nom_existing
                m.add_constraints(gp <= uu * pmax, name=f"uc_pmax_{g.id}")
                m.add_constraints(gp >= uu * (pmax * g.p_min_pu), name=f"uc_pmin_{g.id}")
    elif uc_ids and fixed is not None:
        # LP price pass: commitment fixed as data
        for g in gens:
            if g.id in uc_ids:
                gp = p.sel(g=g.id)
                uvec = xr.DataArray(fixed[g.id], coords=[t_idx], dims=["t"])
                pmax = g.p_nom_existing
                m.add_constraints(gp <= uvec * pmax, name=f"uc_pmax_{g.id}")
                m.add_constraints(gp >= uvec * (pmax * g.p_min_pu), name=f"uc_pmin_{g.id}")

    # must-run units (nuclear): always on at >= min stable level (no binary)
    for g in gens:
        if g.id in mustrun_ids:
            gp = p.sel(g=g.id)
            m.add_constraints(gp >= g.p_nom_existing * g.p_min_pu, name=f"mustrun_{g.id}")

    # commitment transition, min up/down, and ramping (chronological).
    # These are vectorized over t via .shift; the t=0 row references a NaN shift
    # and is automatically masked, which is the correct "no prior state" behavior.
    if binary_uc and uc_ids:
        usel = u.sel(g=uc_ids)
        susel = su.sel(g=uc_ids)
        sdsel = sd.sel(g=uc_ids)
        # state transition for t>=1: u[t] - u[t-1] = startup - shutdown.
        # (drop the t=0 row, whose .shift() is NaN and would be spurious.)
        trans = (usel - usel.shift(t=1) - susel + sdsel).isel(t=slice(1, None))
        m.add_constraints(trans == 0, name="uc_trans")
        # min up: Σ_{j=0..Lup-1} startup[t-j] <= u[t]  (per-unit Lup via shift-sum)
        for g in gens:
            if g.id not in uc_ids:
                continue
            Lup, Ldn = max(g.min_up_h, 1), max(g.min_down_h, 1)
            sup, sdn, uu = su.sel(g=g.id), sd.sel(g=g.id), u.sel(g=g.id)
            if Lup > 1:
                roll = sum(sup.shift(t=j) for j in range(Lup))
                m.add_constraints(roll <= uu, name=f"uc_minup_{g.id}")
            if Ldn > 1:
                roll = sum(sdn.shift(t=j) for j in range(Ldn))
                m.add_constraints(roll <= 1 - uu, name=f"uc_mindn_{g.id}")

    # ramp limits between consecutive chronological timesteps (PCM), vectorized.
    if options.unit_commitment:
        ramp_ids = [g.id for g in gens if not g.is_vre and g.energy_limit_per_period is None]
        if ramp_ids and T > 1:
            ramp_da = xr.DataArray([next(g.ramp_per_h for g in gens if g.id == gid)
                                    for gid in ramp_ids],
                                   coords=[pd.Index(ramp_ids, name="g")], dims=["g"])
            psel = p.sel(g=ramp_ids)
            dp = (psel - psel.shift(t=1)).isel(t=slice(1, None))  # drop NaN t=0 row
            m.add_constraints(dp <= ramp_da, name="ramp_up")
            m.add_constraints(-dp <= ramp_da, name="ramp_dn")

    # hydro energy budget per period
    for g in gens:
        if g.energy_limit_per_period is not None:
            gp = p.sel(g=g.id)
            for pid in np.unique(view.period_ids):
                mask = view.period_ids == pid
                if mask.any():
                    sel = gp.isel(t=np.where(mask)[0])
                    m.add_constraints(sel.sum() <= g.energy_limit_per_period,
                                      name=f"hydro_budget_{g.id}_{pid}")

    # --- storage ---
    sto = view.storages
    sto_ids = [s.id for s in sto]
    if sto_ids:
        s_idx = pd.Index(sto_ids, name="s")
        ch = m.add_variables(lower=0.0, coords=[s_idx, t_idx], name="sto_charge")
        dis = m.add_variables(lower=0.0, coords=[s_idx, t_idx], name="sto_discharge")
        soc = m.add_variables(lower=0.0, coords=[s_idx, t_idx], name="sto_soc")

        p_exist = xr.DataArray([s.p_nom_existing for s in sto], coords=[s_idx], dims=["s"])
        e_exist = xr.DataArray([s.e_nom_existing for s in sto], coords=[s_idx], dims=["s"])
        if options.investment:
            pb_ub = xr.DataArray([s.build_max_mw for s in sto], coords=[s_idx], dims=["s"])
            eb_ub = xr.DataArray([s.build_max_mwh for s in sto], coords=[s_idx], dims=["s"])
            sto_build_p = m.add_variables(lower=0.0, upper=pb_ub, coords=[s_idx], name="sto_build_p")
            sto_build_e = m.add_variables(lower=0.0, upper=eb_ub, coords=[s_idx], name="sto_build_e")
            p_cap = p_exist + sto_build_p
            e_cap = e_exist + sto_build_e
        else:
            sto_build_p = sto_build_e = None
            p_cap = p_exist
            e_cap = e_exist

        m.add_constraints(ch <= p_cap, name="sto_charge_max")
        m.add_constraints(dis <= p_cap, name="sto_discharge_max")
        soc_max_pu = xr.DataArray([s.soc_max_pu for s in sto], coords=[s_idx], dims=["s"])
        soc_min_pu = xr.DataArray([s.soc_min_pu for s in sto], coords=[s_idx], dims=["s"])
        m.add_constraints(soc <= e_cap * soc_max_pu, name="sto_soc_max")
        m.add_constraints(soc >= e_cap * soc_min_pu, name="sto_soc_min")

        # SOC dynamics, cyclic within each representative period. Vectorized per
        # period via .shift (the per-period first row is masked), plus one scalar
        # cyclic-closure constraint linking each period's first step to its last.
        if options.storage_soc_init is not None:
            assert not options.investment, \
                "storage_soc_init is a rolling-horizon (RT) feature"
            w_da = xr.DataArray(view.period_weight, coords=[t_idx], dims=["t"])
            for s in sto:
                sc, sdis, ss = ch.sel(s=s.id), dis.sel(s=s.id), soc.sel(s=s.id)
                e_nom = float(s.e_nom_existing)
                init = float(options.storage_soc_init.get(s.id, 0.5)) * e_nom
                within = (ss - ss.shift(t=1) - s.eff_c * sc * w_da
                          + (1.0 / s.eff_d) * sdis * w_da).isel(t=slice(1, None))
                m.add_constraints(within == 0, name=f"soc_{s.id}_roll")
                m.add_constraints(
                    ss.isel(t=0) - s.eff_c * sc.isel(t=0) * w_da.isel(t=0)
                    + (1.0 / s.eff_d) * sdis.isel(t=0) * w_da.isel(t=0) == init,
                    name=f"soc_init_{s.id}")
            periods = []
        else:
            periods = [(pid, np.where(view.period_ids == pid)[0])
                       for pid in np.unique(view.period_ids)]
        for s in sto:
            sc = ch.sel(s=s.id)
            sdis = dis.sel(s=s.id)
            ss = soc.sel(s=s.id)
            for pid, where in periods:
                if len(where) == 0:
                    continue
                start, end = int(where[0]), int(where[-1])
                sl = slice(start, end + 1)
                ss_sl, sc_sl, sd_sl = ss.isel(t=sl), sc.isel(t=sl), sdis.isel(t=sl)
                # within-period balance for t>start (drop the NaN-shift first row;
                # the period's first step is closed cyclically just below)
                within = (ss_sl - ss_sl.shift(t=1)
                          - s.eff_c * sc_sl + (1.0 / s.eff_d) * sd_sl).isel(t=slice(1, None))
                m.add_constraints(within == 0, name=f"soc_{s.id}_p{pid}")
                # cyclic closure: first step links back to the period's last step
                m.add_constraints(
                    ss.isel(t=start) - ss.isel(t=end)
                    - s.eff_c * sc.isel(t=start) + (1.0 / s.eff_d) * sdis.isel(t=start) == 0,
                    name=f"soc_cyc_{s.id}_p{pid}")
    else:
        ch = dis = soc = None
        sto_build_p = sto_build_e = None

    # --- unserved energy (scarcity) ---
    n_idx = pd.Index(view.nodes, name="n")
    unserved = m.add_variables(lower=0.0, coords=[n_idx, t_idx], name="unserved")

    # --- transmission ---
    flow = None
    theta = None
    line_build = None
    line_ids = [ln.id for ln in view.lines]
    fixed_lines = [ln for ln in view.lines if not ln.is_candidate]
    cand_lines = [ln for ln in view.lines if ln.is_candidate]
    if view.lines:
        l_idx = pd.Index(line_ids, name="l")
        flow = m.add_variables(coords=[l_idx, t_idx], name="flow")

        # DC angle coupling applies only to fixed (existing) lines; candidate
        # additions are transport corridors (no angle equation).
        if view.network_mode == "dc" and fixed_lines:
            theta = m.add_variables(coords=[n_idx, t_idx], name="theta")
            m.add_constraints(theta.sel(n=view.reference_node) == 0, name="ref_angle")
            fl_idx = pd.Index([ln.id for ln in fixed_lines], name="l")
            from_nodes = xr.DataArray([ln.a for ln in fixed_lines], coords=[fl_idx], dims=["l"])
            to_nodes = xr.DataArray([ln.b for ln in fixed_lines], coords=[fl_idx], dims=["l"])
            susc_da = xr.DataArray([1.0 / ln.x for ln in fixed_lines], coords=[fl_idx], dims=["l"])
            theta_a = theta.sel(n=from_nodes)
            theta_b = theta.sel(n=to_nodes)
            m.add_constraints(flow.sel(l=[ln.id for ln in fixed_lines])
                              - susc_da * (theta_a - theta_b) == 0, name="dcpf")

        # thermal limits on fixed lines (fixed rating)
        if fixed_lines:
            fl_idx = pd.Index([ln.id for ln in fixed_lines], name="l")
            rating_da = xr.DataArray([ln.rating for ln in fixed_lines],
                                     coords=[fl_idx], dims=["l"])
            ff = flow.sel(l=[ln.id for ln in fixed_lines])
            m.add_constraints(ff <= rating_da, name="flim_hi")
            m.add_constraints(ff >= -rating_da, name="flim_lo")

        # candidate lines: flow bounded by the built capacity (a decision)
        if cand_lines:
            cids = [ln.id for ln in cand_lines]
            cl_idx = pd.Index(cids, name="l")
            cf = flow.sel(l=cids)
            if options.investment:
                bmax = xr.DataArray([ln.build_max for ln in cand_lines],
                                    coords=[cl_idx], dims=["l"])
                line_build = m.add_variables(lower=0.0, upper=bmax, coords=[cl_idx],
                                             name="line_build")
                m.add_constraints(cf - line_build <= 0, name="candflim_hi")
                m.add_constraints(cf + line_build >= 0, name="candflim_lo")
            else:
                m.add_constraints(cf == 0, name="cand_unbuilt")

    # interface limits: member flows (plus parallel candidate-corridor flow)
    # bounded by the static limit expanded by any *built* parallel capacity
    if flow is not None:
        for iface in view.interfaces:
            members = [mid for mid in iface["members"] if mid in line_ids]
            if not members:
                continue
            expr = sum(flow.sel(l=mid) for mid in members)
            headroom = 0
            for cid in iface.get("parallel_candidates", []):
                if cid in line_ids:
                    expr = expr + flow.sel(l=cid)
                    if line_build is not None:
                        headroom = headroom + line_build.sel(l=cid)
            m.add_constraints(expr - headroom <= iface["limit"],
                              name=f"iface_hi_{iface['id']}")
            m.add_constraints(expr + headroom >= -iface["limit"],
                              name=f"iface_lo_{iface['id']}")

    # --- nodal power balance (its dual is the LMP) ---
    load_da = xr.DataArray(view.load, coords=[n_idx, t_idx], dims=["n", "t"])
    # generation injected per node
    for ni, node in enumerate(view.nodes):
        gen_here = [g.id for g in gens if g.node == node]
        terms = []
        if gen_here:
            terms.append(p.sel(g=gen_here).sum("g"))
        if sto_ids:
            sto_here = [s.id for s in sto if s.node == node]
            if sto_here:
                terms.append(dis.sel(s=sto_here).sum("s"))
                terms.append(-ch.sel(s=sto_here).sum("s"))
        terms.append(unserved.sel(n=node))
        # net flow out of node
        if flow is not None:
            out_lines = [ln.id for ln in view.lines if ln.a == node]
            in_lines = [ln.id for ln in view.lines if ln.b == node]
            if out_lines:
                terms.append(-flow.sel(l=out_lines).sum("l"))
            if in_lines:
                terms.append(flow.sel(l=in_lines).sum("l"))
        lhs = sum(terms[1:], terms[0])
        m.add_constraints(lhs == load_da.sel(n=node), name=f"balance_{node}")

    # --- reserves (soft: a penalized shortfall keeps the model feasible) ---
    res_shortfall = None
    if options.reserves and view.reserve_req is not None and view.reserve_req.max() > 0:
        res_da = xr.DataArray(view.reserve_req, coords=[t_idx], dims=["t"])
        res_gen_ids = [g.id for g in gens if g.reserve_eligible]
        if res_gen_ids:
            headroom_terms = []
            for g in gens:
                if not g.reserve_eligible:
                    continue
                gcap = (cap.sel(g=g.id) if options.investment else float(g.p_nom_existing))
                if options.reserve_curve:
                    # honest headroom (rtops): an outaged or decommitted unit
                    # holds no reserve — scale nameplate by availability and
                    # fixed commitment before subtracting output
                    if g.availability is not None:
                        gcap = gcap * xr.DataArray(g.availability,
                                                   coords=[t_idx], dims=["t"])
                    fc = (options.fixed_commitment or {}).get(g.id)
                    if fc is not None:
                        gcap = gcap * xr.DataArray(np.asarray(fc, dtype=float),
                                                   coords=[t_idx], dims=["t"])
                headroom_terms.append(gcap - p.sel(g=g.id))
            headroom = sum(headroom_terms[1:], headroom_terms[0])
            if options.reserve_curve:
                # one shortfall variable per tranche, each capped at its
                # slice of the requirement; the sum plays the shortfall role
                tranche_vars = []
                prev = 0.0
                for i, (frac, _price) in enumerate(options.reserve_curve):
                    ub = xr.DataArray(view.reserve_req * (frac - prev),
                                      coords=[t_idx], dims=["t"])
                    tranche_vars.append(m.add_variables(
                        lower=0.0, upper=ub, coords=[t_idx],
                        name=f"reserve_short_t{i}"))
                    prev = frac
                res_shortfall = sum(tranche_vars[1:], tranche_vars[0])
                res_shortfall_tranches = tranche_vars
            else:
                res_shortfall = m.add_variables(lower=0.0, coords=[t_idx],
                                                name="reserve_short")
                res_shortfall_tranches = None
            # candidate VRE adds to the requirement in proportion to what is
            # actually *built* (linear in the build variable), not its ceiling
            lhs = headroom + res_shortfall
            if options.investment and gen_build is not None and view.reserve_pct_vre > 0:
                for g in gens:
                    if g.is_candidate and g.availability is not None:
                        avail_da = xr.DataArray(g.availability, coords=[t_idx], dims=["t"])
                        lhs = lhs - gen_build.sel(g=g.id) * (view.reserve_pct_vre * avail_da)
            m.add_constraints(lhs >= res_da, name="reserve_spin")

    # --- emissions cap (annual) ---
    if view.emissions_cap is not None:
        emis_terms = []
        for g in gens:
            if g.emissions_t_per_mwh > 0:
                emis_terms.append((p.sel(g=g.id) * w).sum() * g.emissions_t_per_mwh)
        if emis_terms:
            total_emis = sum(emis_terms[1:], emis_terms[0])
            m.add_constraints(total_emis <= view.emissions_cap, name="emissions_cap")

    # --- RPS (clean energy share) ---
    # RPS/CES is an *annual planning* constraint: enforce it when investment
    # can respond (CEM). A short operational window (PCM/ED) cannot conjure
    # VRE energy that isn't available — applying it there is infeasible by
    # construction, not a lesson.
    rps_short = None
    if view.rps_fraction > 0 and options.investment:
        vre_ids = [g.id for g in gens if g.is_vre]
        if vre_ids:
            vre_energy = (p.sel(g=vre_ids).sum("g") * w).sum()
            total_load_energy = float((view.load.sum(axis=0) * view.period_weight).sum()
                                      / view.annual_divisor)
            # Real RPS policies are enforced with an alternative compliance
            # payment, not an infeasibility: when buildable clean energy runs
            # out (e.g. late planning-study stages), the model pays the ACP on
            # the shortfall instead of failing. The dual/shortfall is the lesson.
            rps_short = m.add_variables(lower=0.0, name="rps_shortfall")
            m.add_constraints(vre_energy + rps_short
                              >= view.rps_fraction * total_load_energy,
                              name="rps")

    # --- objective ---
    obj = 0.0
    if rps_short is not None:
        obj = obj + rps_short * RPS_ACP_PER_MWH
    # operating cost (fuel+vom), weighted and annualized
    for g in gens:
        gp = p.sel(g=g.id)
        cost = g.marginal_cost + view.carbon_price * g.emissions_t_per_mwh
        obj = obj + (gp * w).sum() * cost
    # storage vom
    if sto_ids:
        for s in sto:
            obj = obj + (dis.sel(s=s.id) * w).sum() * s.vom
    # unserved energy at VOLL
    obj = obj + (unserved * w).sum() * view.voll
    # reserve shortfall penalty (below VOLL so energy is served first)
    if res_shortfall is not None:
        if options.reserve_curve:
            for var, (_frac, price) in zip(res_shortfall_tranches,
                                           options.reserve_curve):
                obj = obj + (var * w).sum() * price
        else:
            obj = obj + (res_shortfall * w).sum() * (view.voll * 0.5)
    # startup + no-load (PCM, only when commitment is decided here)
    if binary_uc:
        for g in gens:
            if g.id in uc_ids:
                obj = obj + (su.sel(g=g.id) * w).sum() * g.start_cost
                obj = obj + (u.sel(g=g.id) * w).sum() * g.no_load_cost
    # investment (CEM): annualized capex + FOM
    if options.investment:
        for g in gens:
            if g.is_candidate:
                obj = obj + gen_build.sel(g=g.id) * g.capex_annual_per_mw
        if sto_ids:
            for s in sto:
                if s.is_candidate:
                    obj = obj + sto_build_p.sel(s=s.id) * s.capex_annual_per_mw
                    obj = obj + sto_build_e.sel(s=s.id) * s.capex_annual_per_mwh
        if line_build is not None:
            for ln in cand_lines:
                obj = obj + line_build.sel(l=ln.id) * ln.capex_annual_per_mw

    m.add_objective(obj)

    return BuiltModel(m=m, view=view, options=options, gen_ids=gen_ids,
                      sto_ids=sto_ids, line_ids=line_ids,
                      meta={"uc_ids": uc_ids, "binary_uc": binary_uc})


# --- solve + extraction helpers ---------------------------------------------


def solve_model(built: BuiltModel, **kwargs) -> str:
    """Solve with HiGHS quietly. Returns the status string.

    linopy passes extra kwargs straight through to HiGHS as solver options.
    """
    kwargs.setdefault("output_flag", False)
    # hard ceiling so a pathological MILP can't hold an API worker forever
    kwargs.setdefault("time_limit", 300.0)
    built.m.solve(solver_name="highs", progress=False, **kwargs)
    return str(built.m.status)


def _var(built: BuiltModel, name: str):
    return built.m.variables[name].solution if name in built.m.variables else None


def realized_capacity_factor(energy_mwh: float, capacity_mw: float,
                             total_hours: float) -> float:
    """CF = energy / (capacity * hours) — the canonical traceable output (4.6)."""
    denom = capacity_mw * total_hours
    return float(energy_mwh / denom) if denom > 0 else 0.0


def collect_dispatch(built: BuiltModel) -> "DispatchResult":
    """Build a DispatchResult from the solved model (Section 4.6)."""
    from ..schema import DispatchResult, Provenance

    view = built.view
    w = view.period_weight / view.annual_divisor
    total_hours = float(view.period_weight.sum() / view.annual_divisor)

    gp = _var(built, "gen_p")             # dims (g, t)
    res = DispatchResult(engine=built.options.label, scenario_id="")
    res.timesteps = list(range(view.T))
    res.period_weights = view.period_weight.tolist()

    # capacities (existing + built)
    build_sol = _var(built, "gen_build")
    cap_by_gen: dict[str, float] = {}
    for g in view.gens:
        cap = g.p_nom_existing
        if build_sol is not None and g.id in build_sol.coords["g"].values:
            cap += float(build_sol.sel(g=g.id))
        cap_by_gen[g.id] = cap

    for g in view.gens:
        series = gp.sel(g=g.id).values
        res.generation_mw[g.id] = [float(x) for x in series]
        energy = float((series * view.period_weight).sum() / view.annual_divisor)
        cf = realized_capacity_factor(energy, cap_by_gen[g.id], total_hours)
        res.realized_capacity_factor[g.id] = cf
        # curtailment for VRE: available - dispatched
        if g.availability is not None:
            avail_mw = g.availability * cap_by_gen[g.id]
            res.curtailment_mw[g.id] = [float(max(a - d, 0.0))
                                        for a, d in zip(avail_mw, series)]

    # storage
    ch = _var(built, "sto_charge")
    dis = _var(built, "sto_discharge")
    soc = _var(built, "sto_soc")
    if ch is not None:
        for s in view.storages:
            res.charge_mw[s.id] = [float(x) for x in ch.sel(s=s.id).values]
            res.discharge_mw[s.id] = [float(x) for x in dis.sel(s=s.id).values]
            res.soc_mwh[s.id] = [float(x) for x in soc.sel(s=s.id).values]

    # unserved energy
    uns = _var(built, "unserved")
    if uns is not None:
        for node in view.nodes:
            arr = uns.sel(n=node).values
            if float((arr * view.period_weight).sum()) > 1e-6:
                res.unserved_mw[node] = [float(x) for x in arr]

    res.total_cost = float(built.m.objective.value)
    res.provenance = Provenance(
        engine=built.options.label,
        governing=["nodal power balance", "generator capacity x availability",
                   "storage SOC dynamics"],
        notes="realized_capacity_factor = annual energy / (capacity x 8760)")
    return res


def collect_network(built: BuiltModel) -> "NetworkResult":
    """Flows and LMPs (duals of the balance constraints) from a solved LP."""
    from ..schema import NetworkResult, Provenance

    view = built.view
    nr = NetworkResult(engine=built.options.label)
    # time-averaged LMP per node from the balance-constraint dual
    for node in view.nodes:
        cname = f"balance_{node}"
        if cname in built.m.constraints:
            try:
                dual = built.m.constraints[cname].dual.values
                # dual_h = weight_h * price_h (the objective weights each hour
                # by w). The time-averaged $/MWh is the weight-average of the
                # per-hour prices = sum(price*w)/sum(w) = sum(dual)/sum(w).
                w = view.period_weight / view.annual_divisor
                wsum = w.sum()
                nr.nodal_price[node] = float(dual.sum() / wsum) if wsum else float(dual.mean())
                # per-hour $/MWh for playback: divide out each hour's weight
                safe_w = np.where(w > 1e-12, w, 1.0)
                nr.nodal_price_t[node] = [round(float(v), 2)
                                          for v in (dual / safe_w)]
            except (AttributeError, KeyError):
                pass
    # time-averaged + per-hour flow per line
    flow = _var(built, "flow")
    if flow is not None:
        for lid in built.line_ids:
            series = flow.sel(l=lid).values
            nr.flow_mw[lid] = float(np.abs(series).mean())
            nr.flow_t_mw[lid] = [round(float(v), 2) for v in series]
    # binding interface duals
    for iface in view.interfaces:
        for suffix in ("hi", "lo"):
            cname = f"iface_{suffix}_{iface['id']}"
            if cname in built.m.constraints:
                try:
                    d = float(np.abs(built.m.constraints[cname].dual.values).max())
                    if d > 1e-6:
                        nr.dual_values[iface["id"]] = d
                except (AttributeError, KeyError):
                    pass
    nr.provenance = Provenance(engine=built.options.label,
                               governing=["LMP = dual of nodal power balance"])
    return nr
