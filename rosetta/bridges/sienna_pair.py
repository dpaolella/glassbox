"""Bridges between the Sienna schema mirror and PyPSA / Glassbox.

The taxonomy asymmetry, in both directions:

  * INTO sienna: every open label must resolve to a closed (prime_mover, fuel)
    pair — unresolvable labels are counted as manual_mapping_required.
  * OUT of sienna: closed pairs flatten to canonical open labels trivially.

Reserves are the scope asymmetry: Sienna holds them natively (StaticReserve),
PyPSA cannot hold them at all — so on a glassbox -> sienna -> glassbox route
reserves survive by *translation*, while on glassbox -> pypsa -> glassbox they
survive only by riding the sidecar. Both routes end with reserves, but the
manifests tell two different stories, which is the point of the bench.
"""

from __future__ import annotations

import math

import numpy as np

from ..core import Coverage, Payload, bridge
from ..schemas.sienna import (EnergyReservoirStorage, HydroDispatch, PowerLoad,
                              PrimeMover, RenewableDispatch, SiennaBus,
                              SiennaLine, SiennaSystem, StaticReserve,
                              ThermalFuel, ThermalStandard)

HOURS_PER_YEAR = 8760

# open label -> closed pair (shared by the pypsa- and glassbox-facing legs)
LABEL_TO_PAIR: dict[str, tuple[PrimeMover, ThermalFuel | None]] = {
    "coal": (PrimeMover.ST, ThermalFuel.COAL),
    "ccgt": (PrimeMover.CC, ThermalFuel.NATURAL_GAS),
    "gas": (PrimeMover.CC, ThermalFuel.NATURAL_GAS),
    "ocgt": (PrimeMover.CT, ThermalFuel.NATURAL_GAS),
    "nuclear": (PrimeMover.ST, ThermalFuel.NUCLEAR),
    "geothermal": (PrimeMover.ST, ThermalFuel.GEOTHERMAL),
    "biomass": (PrimeMover.ST, ThermalFuel.WASTE_BIOMASS),
    "wind": (PrimeMover.WT, None),
    "onwind": (PrimeMover.WT, None),
    "offwind": (PrimeMover.WS, None),
    "solar": (PrimeMover.PVe, None),
    "solar_pv": (PrimeMover.PVe, None),
    "pv": (PrimeMover.PVe, None),
}
# closed pair -> canonical open label (the trivial direction)
PAIR_TO_LABEL = {
    (PrimeMover.ST, ThermalFuel.COAL): "coal",
    (PrimeMover.CC, ThermalFuel.NATURAL_GAS): "ccgt",
    (PrimeMover.GT, ThermalFuel.NATURAL_GAS): "ocgt",
    (PrimeMover.CT, ThermalFuel.NATURAL_GAS): "ocgt",
    (PrimeMover.ST, ThermalFuel.NUCLEAR): "nuclear",
    (PrimeMover.ST, ThermalFuel.GEOTHERMAL): "geothermal",
    (PrimeMover.ST, ThermalFuel.WASTE_BIOMASS): "biomass",
}
RENEWABLE_PM = {PrimeMover.WT: "wind", PrimeMover.WS: "wind",
                PrimeMover.PVe: "solar_pv"}


def _to_pair(label: str, cov: Coverage, entity: str):
    key = (label or "").strip().lower()
    if key in LABEL_TO_PAIR:
        return LABEL_TO_PAIR[key]
    cov.manual_mapping_required.append({
        "entity": entity, "label": label,
        "note": "open label has no closed (prime_mover, fuel) home; "
                "defaulted to (OT, OTHER)"})
    return PrimeMover.OT, ThermalFuel.OTHER


# ---------------------------------------------------------------------------
# pypsa -> sienna
# ---------------------------------------------------------------------------


@bridge("pypsa", "sienna", notes="free-text carriers forced onto closed enums")
def pypsa_to_sienna(payload: Payload, opts: dict) -> Payload:
    n = payload.native
    cov = Coverage(bridge="pypsa->sienna")
    hours = max(len(n.snapshots), 1)
    sys = SiennaSystem(name="translated")

    for i, bid in enumerate(n.buses.index):
        row = n.buses.loc[bid]
        sys.buses.append(SiennaBus(name=str(bid), number=i,
                                   base_voltage_kv=float(row["v_nom"]),
                                   x=float(row["x"]), y=float(row["y"])))
        cov.count("buses")
    for lid in n.lines.index:
        row = n.lines.loc[lid]
        vn = float(n.buses.loc[row["bus0"], "v_nom"]) or 100.0
        zbase = vn ** 2 / sys.base_power_mva
        sys.lines.append(SiennaLine(
            name=str(lid), from_bus=str(row["bus0"]), to_bus=str(row["bus1"]),
            r_pu=float(row["r"]) / zbase, x_pu=max(float(row["x"]) / zbase, 1e-5),
            rating_mva=float(row["s_nom"])))
        cov.count("lines")

    for gid in n.generators.index:
        row = n.generators.loc[gid]
        if bool(row.get("p_nom_extendable", False)):
            payload.sidecar.park("pypsa:extendable_generators", str(gid),
                                 {"bus": str(row["bus"]),
                                  "carrier": str(row.get("carrier", "")),
                                  "p_nom_max": float(row.get("p_nom_max", 0.0)
                                                     if math.isfinite(row.get("p_nom_max", 0.0)) else 0.0),
                                  "capital_cost": float(row.get("capital_cost", 0.0))},
                                 "pypsa", "this Sienna mirror carries no "
                                          "Investments domain")
            cov.parked.append({"concept": "pypsa:extendable_generators", "n": 1,
                               "why": "investment options not in the mirror"})
            continue
        carrier = str(row.get("carrier", "") or "")
        if carrier.lower() == "hydro":
            e_sum = float(row.get("e_sum_max", math.inf))
            sys.hydro.append(HydroDispatch(
                name=str(gid), bus=str(row["bus"]),
                active_power_limits_max_mw=float(row["p_nom"]),
                storage_capacity_mwh=(e_sum * HOURS_PER_YEAR / hours
                                      if math.isfinite(e_sum) else None)))
            cov.count("hydro")
            continue
        pm, fuel = _to_pair(carrier, cov, str(gid))
        if pm in RENEWABLE_PM:
            series_id = None
            if len(n.generators_t.p_max_pu.columns) and \
                    gid in n.generators_t.p_max_pu.columns:
                series_id = f"availability__{gid}"
                sys.series[series_id] = [float(v) for v in
                                         n.generators_t.p_max_pu[gid].values]
            sys.renewable.append(RenewableDispatch(
                name=str(gid), bus=str(row["bus"]), prime_mover=pm,
                rating_mw=float(row["p_nom"]),
                variable_cost_per_mwh=float(row.get("marginal_cost", 0.0)),
                availability_series=series_id))
        else:
            sys.thermal.append(ThermalStandard(
                name=str(gid), bus=str(row["bus"]), prime_mover=pm,
                fuel=fuel or ThermalFuel.OTHER,
                active_power_limits_max_mw=float(row["p_nom"]),
                variable_cost_per_mwh=float(row.get("marginal_cost", 0.0))))
        cov.count("generators")

    for sid in n.storage_units.index:
        row = n.storage_units.loc[sid]
        p = float(row["p_nom"])
        sys.storage.append(EnergyReservoirStorage(
            name=str(sid), bus=str(row["bus"]),
            input_active_power_limit_mw=p, output_active_power_limit_mw=p,
            storage_capacity_mwh=p * float(row.get("max_hours", 1.0)),
            efficiency_in=float(row.get("efficiency_store", 0.95)),
            efficiency_out=float(row.get("efficiency_dispatch", 0.95))))
        cov.count("storage")

    for lid in n.loads.index:
        row = n.loads.loc[lid]
        series_id = None
        peak = float(row.get("p_set", 0.0) or 0.0)
        if len(n.loads_t.p_set.columns) and lid in n.loads_t.p_set.columns:
            arr = n.loads_t.p_set[lid].values
            peak = float(np.max(arr))
            series_id = f"demand__{lid}"
            sys.series[series_id] = [float(v) for v in arr]
        sys.loads.append(PowerLoad(name=str(lid), bus=str(row["bus"]),
                                   max_active_power_mw=peak,
                                   demand_series=series_id))
        cov.count("loads")

    if len(n.links):
        cov.dropped.append({"what": f"{len(n.links)} link(s)",
                            "why": "this Sienna mirror has no controllable "
                                   "branch component"})

    # restore reserves parked by an earlier leg — Sienna holds them natively
    for entry in payload.sidecar.take("glassbox:reserve_products"):
        fixed = float(entry.payload.get("requirement_rule", {}).get("fixed_mw", 0.0))
        sys.reserves.append(StaticReserve(name=entry.entity_id,
                                          requirement_mw=fixed))
        cov.restored.append({"concept": entry.concept, "n": 1})
        rule = entry.payload.get("requirement_rule", {})
        if any(k in rule for k in ("pct_load", "pct_vre")):
            cov.approximated.append({
                "what": f"reserve rule ({entry.entity_id})",
                "how": "percentage terms flattened; only the fixed-MW part "
                       "lands in StaticReserve"})

    out = Payload("sienna", sys, sidecar=payload.sidecar,
                  coverage=list(payload.coverage))
    out.hop(cov)
    return out


# ---------------------------------------------------------------------------
# sienna -> pypsa
# ---------------------------------------------------------------------------


@bridge("sienna", "pypsa", notes="closed pairs flatten to open labels trivially")
def sienna_to_pypsa(payload: Payload, opts: dict) -> Payload:
    import pandas as pd
    import pypsa

    sys: SiennaSystem = payload.native
    cov = Coverage(bridge="sienna->pypsa")
    hours = int(opts.get("hours", 168))
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(hours))

    for b in sys.buses:
        n.add("Bus", b.name, v_nom=b.base_voltage_kv or 100.0, x=b.x, y=b.y)
        cov.count("buses")
    kv = {b.name: (b.base_voltage_kv or 100.0) for b in sys.buses}
    for ln in sys.lines:
        zbase = kv.get(ln.from_bus, 100.0) ** 2 / sys.base_power_mva
        n.add("Line", ln.name, bus0=ln.from_bus, bus1=ln.to_bus,
              r=ln.r_pu * zbase, x=max(ln.x_pu, 1e-5) * zbase,
              s_nom=ln.rating_mva)
        cov.count("lines")

    def series_or_none(sid):
        if sid and sid in sys.series:
            arr = np.asarray(sys.series[sid], dtype=float)
            if len(arr) >= hours:
                return pd.Series(arr[:hours], index=n.snapshots)
            reps = int(np.ceil(hours / max(len(arr), 1)))
            return pd.Series(np.tile(arr, reps)[:hours], index=n.snapshots)
        return None

    for t in sys.thermal:
        label = PAIR_TO_LABEL.get((t.prime_mover, t.fuel),
                                  f"{t.prime_mover.value}:{t.fuel.value}".lower())
        n.add("Generator", t.name, bus=t.bus,
              p_nom=t.active_power_limits_max_mw, carrier=label,
              marginal_cost=t.variable_cost_per_mwh)
        cov.count("generators")
    for r in sys.renewable:
        kwargs = dict(bus=r.bus, p_nom=r.rating_mw,
                      carrier=RENEWABLE_PM.get(r.prime_mover, "wind"),
                      marginal_cost=r.variable_cost_per_mwh)
        s = series_or_none(r.availability_series)
        if s is not None:
            kwargs["p_max_pu"] = s
        n.add("Generator", r.name, **kwargs)
        cov.count("generators")
    for h in sys.hydro:
        budget = (h.storage_capacity_mwh * hours / HOURS_PER_YEAR
                  if h.storage_capacity_mwh else math.inf)
        n.add("Generator", h.name, bus=h.bus,
              p_nom=h.active_power_limits_max_mw, carrier="hydro",
              marginal_cost=1.0, e_sum_max=budget)
        cov.count("hydro")
    for s in sys.storage:
        p = s.output_active_power_limit_mw
        n.add("StorageUnit", s.name, bus=s.bus, p_nom=p,
              max_hours=(s.storage_capacity_mwh / p if p > 0 else 1.0),
              efficiency_store=s.efficiency_in,
              efficiency_dispatch=s.efficiency_out,
              cyclic_state_of_charge=True)
        cov.count("storage")

    for ld in sys.loads:
        s = series_or_none(ld.demand_series)
        if s is None:
            s = pd.Series(np.full(hours, ld.max_active_power_mw),
                          index=n.snapshots)
            cov.invented.append({"what": f"demand shape ({ld.name})",
                                 "value": "flat", "why": "static load"})
        n.add("Load", ld.name, bus=ld.bus, p_set=s)
        cov.count("loads")

    for rv in sys.reserves:
        payload.sidecar.park("sienna:reserves", rv.name,
                             rv.model_dump(mode="json"), "sienna",
                             "PyPSA has no reserve component")
        cov.parked.append({"concept": "sienna:reserves", "n": 1,
                           "why": "no PyPSA reserve component"})

    out = Payload("pypsa", n, sidecar=payload.sidecar,
                  coverage=list(payload.coverage))
    out.hop(cov)
    return out


# ---------------------------------------------------------------------------
# sienna -> glassbox / glassbox -> sienna
# ---------------------------------------------------------------------------


@bridge("sienna", "glassbox", notes="closed pair -> closed enum; reserves translate")
def sienna_to_glassbox(payload: Payload, opts: dict) -> Payload:
    from glassbox.schema import (ACLine, Bus, BusType, Generator, GenTechnology,
                                 Hydro, Load, ReserveProduct, Storage,
                                 TimeSeries, TimeSeriesKind, World, Zone)

    sys: SiennaSystem = payload.native
    cov = Coverage(bridge="sienna->glassbox")
    world = World(id="translated", name="translated via rosetta",
                  base_power_mva=sys.base_power_mva)
    store = world.time_series_store

    invent_coords = all(b.x == 0 and b.y == 0 for b in sys.buses) and sys.buses
    for i, b in enumerate(sys.buses):
        if invent_coords:
            ang = 2 * math.pi * i / max(len(sys.buses), 1)
            bx, by = 100 * math.cos(ang), 100 * math.sin(ang)
        else:
            bx, by = b.x, b.y
        world.buses.append(Bus(
            id=b.name, name=b.name, base_kv=b.base_voltage_kv or 100.0,
            zone_id="Z1", x=bx, y=by,
            bus_type=BusType.SLACK if b.bus_type == "REF" else BusType.PQ))
        cov.count("buses")
    if invent_coords:
        cov.invented.append({"what": "bus coordinates", "value": "ring layout",
                             "why": "source has no geodata"})

    total_load = sum(ld.max_active_power_mw for ld in sys.loads)
    for ln in sys.lines:
        rating = ln.rating_mva or max(total_load * 2, 100.0)
        if not ln.rating_mva:
            cov.invented.append({"what": f"line rating ({ln.name})",
                                 "value": rating, "why": "unrated line"})
        world.ac_lines.append(ACLine(
            id=ln.name, name=ln.name, from_bus_id=ln.from_bus,
            to_bus_id=ln.to_bus, r=ln.r_pu, x=max(ln.x_pu, 1e-5),
            rating_normal_mva=rating, rating_emergency_mva=rating * 1.2,
            rating_lt_mva=rating * 1.1))
        cov.count("lines")

    def gb_tech(pm: PrimeMover, fuel, entity: str) -> str:
        if pm in RENEWABLE_PM:
            return RENEWABLE_PM[pm]
        label = PAIR_TO_LABEL.get((pm, fuel))
        if label:
            return label
        cov.manual_mapping_required.append({
            "entity": entity, "label": f"({pm.value}, {getattr(fuel, 'value', None)})",
            "note": "closed pair outside the enum-to-enum table; "
                    "defaulted to ccgt"})
        return "ccgt"

    for t in sys.thermal:
        world.generators.append(Generator(
            id=t.name, name=t.name, bus_id=t.bus,
            technology=GenTechnology(gb_tech(t.prime_mover, t.fuel, t.name)),
            p_max_mw=t.active_power_limits_max_mw,
            vom_per_mwh=t.variable_cost_per_mwh))
        cov.count("generators")
    for r in sys.renewable:
        avail_id = None
        if r.availability_series and r.availability_series in sys.series:
            arr = np.asarray(sys.series[r.availability_series], dtype=float)
            reps = int(np.ceil(HOURS_PER_YEAR / max(len(arr), 1)))
            avail_id = f"availability__{r.name}"
            store.add(TimeSeries(id=avail_id, kind=TimeSeriesKind.AVAILABILITY),
                      np.tile(arr, reps)[:HOURS_PER_YEAR])
        world.generators.append(Generator(
            id=r.name, name=r.name, bus_id=r.bus,
            technology=GenTechnology(RENEWABLE_PM.get(r.prime_mover, "wind")),
            prime_mover="inverter", p_max_mw=r.rating_mw,
            vom_per_mwh=r.variable_cost_per_mwh,
            availability_profile_id=avail_id))
        cov.count("generators")
    for h in sys.hydro:
        world.hydro_units.append(Hydro(
            id=h.name, name=h.name, bus_id=h.bus,
            p_max_mw=h.active_power_limits_max_mw,
            reservoir_energy_mwh=h.storage_capacity_mwh or 0.0))
        cov.count("hydro")
    for s in sys.storage:
        world.storage_units.append(Storage(
            id=s.name, name=s.name, bus_id=s.bus, technology="battery",
            p_charge_max_mw=s.input_active_power_limit_mw,
            p_discharge_max_mw=s.output_active_power_limit_mw,
            energy_capacity_mwh=s.storage_capacity_mwh,
            efficiency_charge=s.efficiency_in,
            efficiency_discharge=s.efficiency_out))
        cov.count("storage")

    for ld in sys.loads:
        if ld.demand_series and ld.demand_series in sys.series:
            arr = np.asarray(sys.series[ld.demand_series], dtype=float)
            reps = int(np.ceil(HOURS_PER_YEAR / max(len(arr), 1)))
            arr = np.tile(arr, reps)[:HOURS_PER_YEAR]
        else:
            arr = np.full(HOURS_PER_YEAR, ld.max_active_power_mw)
            cov.invented.append({"what": f"demand shape ({ld.name})",
                                 "value": "flat 8760h profile",
                                 "why": "source load is a static peak"})
        pid = f"demand__{ld.name}"
        store.add(TimeSeries(id=pid, kind=TimeSeriesKind.DEMAND, unit="MW"), arr)
        world.loads.append(Load(id=ld.name, name=ld.name, bus_id=ld.bus,
                                demand_profile_id=pid))
        cov.count("loads")

    # reserves translate natively (Sienna -> glassbox both hold the concept)
    for rv in sys.reserves:
        world.reserve_products.append(ReserveProduct(
            id=rv.name, requirement_rule={"fixed_mw": rv.requirement_mw}))
        cov.count("reserve_products")
    # ... and restore any that rode the sidecar from an earlier pypsa leg
    for entry in payload.sidecar.take("sienna:reserves"):
        world.reserve_products.append(ReserveProduct(
            id=entry.entity_id,
            requirement_rule={"fixed_mw": entry.payload.get("requirement_mw", 0.0)}))
        cov.restored.append({"concept": entry.concept, "n": 1})
    for entry in payload.sidecar.take("glassbox:"):
        from glassbox.schema import ENTITY_MODELS
        name_by_coll = {
            "reserve_products": "ReserveProduct", "policies": "Policy",
            "interfaces": "Interface", "resource_potentials": "ResourcePotential",
            "zones": "Zone", "expansion_candidates": "ExpansionCandidate",
        }
        coll = entry.concept.split(":", 1)[1]
        cls = ENTITY_MODELS[name_by_coll[coll]]
        getattr(world, coll).append(cls.model_validate(entry.payload))
        cov.restored.append({"concept": entry.concept, "n": 1})

    if not world.zones:
        world.zones.append(Zone(id="Z1", name="Zone 1",
                                member_bus_ids=[b.id for b in world.buses]))
    ref = next((b.id for b in world.buses if b.bus_type == BusType.SLACK),
               world.buses[0].id if world.buses else "")
    world.reference_bus_id = ref

    out = Payload("glassbox", world, sidecar=payload.sidecar,
                  coverage=list(payload.coverage))
    out.hop(cov)
    return out


@bridge("glassbox", "sienna", notes="closed enum -> closed pair; reserves translate")
def glassbox_to_sienna(payload: Payload, opts: dict) -> Payload:
    world = payload.native
    cov = Coverage(bridge="glassbox->sienna")
    sys = SiennaSystem(name=world.name or world.id,
                       base_power_mva=world.base_power_mva)
    store = world.time_series_store

    for i, b in enumerate(world.buses):
        sys.buses.append(SiennaBus(
            name=b.id, number=i, base_voltage_kv=b.base_kv,
            bus_type="REF" if b.id == world.reference_bus_id else "PQ",
            load_zone=b.zone_id, x=b.x, y=b.y))
        cov.count("buses")
    for ln in world.ac_lines:
        if not ln.in_service:
            continue
        sys.lines.append(SiennaLine(
            name=ln.id, from_bus=ln.from_bus_id, to_bus=ln.to_bus_id,
            r_pu=ln.r, x_pu=max(ln.x, 1e-5), rating_mva=ln.rating_normal_mva))
        cov.count("lines")

    for g in world.generators:
        if not g.in_service or g.status.value == "retired":
            continue
        tech = g.technology.value
        pm, fuel = _to_pair(tech, cov, g.id)
        if pm in RENEWABLE_PM:
            series_id = None
            if g.availability_profile_id and g.availability_profile_id in store:
                series_id = g.availability_profile_id
                sys.series[series_id] = [float(v) for v in
                                         store.get(series_id)[:HOURS_PER_YEAR]]
            sys.renewable.append(RenewableDispatch(
                name=g.id, bus=g.bus_id, prime_mover=pm,
                rating_mw=g.p_max_mw, variable_cost_per_mwh=g.vom_per_mwh,
                availability_series=series_id))
        else:
            sys.thermal.append(ThermalStandard(
                name=g.id, bus=g.bus_id, prime_mover=pm,
                fuel=fuel or ThermalFuel.OTHER,
                active_power_limits_max_mw=g.p_max_mw,
                variable_cost_per_mwh=g.vom_per_mwh))
        cov.count("generators")
    if world.generators:
        cov.approximated.append({
            "what": "UC parameters and fuel objects",
            "how": "min up/down, start cost, heat rates flattened to a "
                   "variable cost in this mirror subset"})

    for h in world.hydro_units:
        if not h.in_service:
            continue
        sys.hydro.append(HydroDispatch(
            name=h.id, bus=h.bus_id,
            active_power_limits_max_mw=h.p_max_mw,
            storage_capacity_mwh=h.reservoir_energy_mwh or None))
        cov.count("hydro")
    for s in world.storage_units:
        if not s.in_service:
            continue
        sys.storage.append(EnergyReservoirStorage(
            name=s.id, bus=s.bus_id,
            input_active_power_limit_mw=s.p_charge_max_mw,
            output_active_power_limit_mw=s.p_discharge_max_mw,
            storage_capacity_mwh=s.energy_capacity_mwh,
            efficiency_in=s.efficiency_charge,
            efficiency_out=s.efficiency_discharge))
        cov.count("storage")

    for ld in world.loads:
        series_id, peak = None, 0.0
        if ld.demand_profile_id and ld.demand_profile_id in store:
            arr = store.get(ld.demand_profile_id)[:HOURS_PER_YEAR] * world.demand_scale
            series_id = ld.demand_profile_id
            sys.series[series_id] = [float(v) for v in arr]
            peak = float(np.max(arr))
        sys.loads.append(PowerLoad(name=ld.id, bus=ld.bus_id,
                                   max_active_power_mw=peak,
                                   demand_series=series_id))
        cov.count("loads")
    if world.loads:
        cov.dropped.append({"what": "VOLL per load",
                            "why": "no unserved-energy price in the mirror"})

    # reserves: the concept Sienna holds natively that PyPSA cannot
    for rp in world.reserve_products:
        rule = rp.requirement_rule or {}
        sys.reserves.append(StaticReserve(
            name=rp.id, requirement_mw=float(rule.get("fixed_mw", 0.0))))
        cov.count("reserve_products")
        if any(k in rule for k in ("pct_load", "pct_vre")):
            cov.approximated.append({
                "what": f"reserve rule ({rp.id})",
                "how": "percentage terms flattened; only the fixed-MW part "
                       "lands in StaticReserve"})

    # park what the mirror can't hold, restorable by a later glassbox leg
    for coll in ("policies", "interfaces", "resource_potentials",
                 "expansion_candidates", "zones"):
        items = getattr(world, coll)
        for item in items:
            payload.sidecar.park(f"glassbox:{coll}", item.id,
                                 item.model_dump(mode="json"), "glassbox",
                                 "outside this Sienna mirror's subset")
        if items:
            cov.parked.append({"concept": f"glassbox:{coll}", "n": len(items),
                               "why": "outside the mirror subset"})

    out = Payload("sienna", sys, sidecar=payload.sidecar,
                  coverage=list(payload.coverage))
    out.hop(cov)
    return out
