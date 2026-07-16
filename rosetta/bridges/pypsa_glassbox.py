"""PyPSA <-> Glassbox bridges.

The most instructive pair on the bench: an open free-text-carrier schema vs a
closed-enum, facet-tagged one. Both directions keep the full coverage ledger:

  * glassbox -> pypsa: carriers absorb technology enums losslessly (the open
    hub's ingest advantage, demonstrated), but reserves, policies, interfaces,
    zones and supply curves have NO PyPSA home and are parked in the sidecar.
  * pypsa -> glassbox: free-text carriers must be mapped onto the closed
    GenTechnology enum (the opinionated spoke's ingest cost, counted per
    entity), and any 'glassbox:*' sidecar entries are RESTORED — so a
    glassbox -> pypsa-hub -> glassbox roundtrip gets its reserves back, and
    the manifest shows they only survived by riding the sidecar.
"""

from __future__ import annotations

import math

import numpy as np

from ..core import Bridge, Coverage, Payload, bridge

HOURS_PER_YEAR = 8760

# open carrier label -> closed glassbox GenTechnology value. 'gas' is genuinely
# ambiguous (CC vs CT) — mapping it is a judgement call, and the bridge says so.
CARRIER_TO_GB = {
    "coal": "coal", "lignite": "coal",
    "ccgt": "ccgt", "gas": "ccgt", "natural_gas": "ccgt", "cc": "ccgt",
    "ocgt": "ocgt", "oil": "ocgt", "diesel": "ocgt",
    "nuclear": "nuclear",
    "wind": "wind", "onwind": "wind", "offwind": "wind",
    "offwind-ac": "wind", "offwind-dc": "wind",
    "solar": "solar_pv", "solar_pv": "solar_pv", "pv": "solar_pv",
    "geothermal": "geothermal",
    "biomass": "biomass", "biogas": "biomass",
}
AMBIGUOUS_CARRIERS = {"gas", "natural_gas", "oil"}
VRE_TECHS = {"wind", "solar_pv"}

# glassbox collections with no PyPSA representation: parked, restorable
PARKED_COLLECTIONS = ("reserve_products", "policies", "interfaces",
                      "resource_potentials", "zones")


def _crf(rate: float, life: int) -> float:
    if rate <= 0:
        return 1.0 / life
    f = (1 + rate) ** life
    return rate * f / (f - 1)


def _gb_marginal_cost(world, g) -> float:
    if g.technology.value in VRE_TECHS:
        return g.vom_per_mwh
    hr = g.heat_rate_mmbtu_per_mwh or 0.0
    price = 0.0
    if g.fuel_id:
        fuel = next((f for f in world.fuels if f.id == g.fuel_id), None)
        price = fuel.price_per_mmbtu if fuel else 0.0
    return hr * price + g.vom_per_mwh


# ---------------------------------------------------------------------------
# glassbox -> pypsa
# ---------------------------------------------------------------------------


@bridge("glassbox", "pypsa",
        notes="open carriers absorb tech enums; reserves/policies/zones parked")
def glassbox_to_pypsa(payload: Payload, opts: dict) -> Payload:
    import pandas as pd
    import pypsa

    world = payload.native
    hours = int(opts.get("hours", 168))
    cov = Coverage(bridge="glassbox->pypsa")
    store = world.time_series_store
    sb = world.base_power_mva

    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(hours))

    kv = {}
    for b in world.buses:
        kv[b.id] = b.base_kv
        n.add("Bus", b.id, v_nom=b.base_kv, x=b.x, y=b.y)
        cov.count("buses")

    for ln in world.ac_lines:
        if not ln.in_service:
            continue
        zbase = (kv.get(ln.from_bus_id, 100.0) ** 2) / sb
        n.add("Line", ln.id, bus0=ln.from_bus_id, bus1=ln.to_bus_id,
              x=max(ln.x, 1e-5) * zbase, r=ln.r * zbase,
              s_nom=ln.rating_normal_mva)
        cov.count("ac_lines")
    if world.ac_lines:
        cov.dropped.append({"what": "emergency / long-term line ratings",
                            "why": "PyPSA Line has a single s_nom"})

    for tr in world.transformers:
        # glassbox x is pu on the system base; PyPSA transformer x is pu on s_nom
        n.add("Transformer", tr.id, bus0=tr.from_bus_id, bus1=tr.to_bus_id,
              s_nom=tr.rating_mva, x=max(tr.x, 1e-5) * tr.rating_mva / sb,
              model="pi")
        cov.count("transformers")

    ref_bus = world.reference_bus_id
    for g in world.generators:
        if not g.in_service or g.status.value == "retired":
            continue
        tech = g.technology.value
        kw = dict(bus=g.bus_id, p_nom=g.p_max_mw, carrier=tech,
                  marginal_cost=_gb_marginal_cost(world, g))
        if tech in VRE_TECHS and g.availability_profile_id and \
                g.availability_profile_id in store:
            arr = store.get(g.availability_profile_id)[:hours]
            kw["p_max_pu"] = pd.Series(np.clip(arr, 0.0, None),
                                       index=n.snapshots)
        if g.bus_id == ref_bus:
            kw["control"] = "Slack"
        n.add("Generator", g.id, **kw)
        cov.count("generators")
    if world.generators:
        cov.approximated.append({
            "what": "fuel-price decomposition",
            "how": "heat_rate x fuel price + VOM folded into one marginal_cost; "
                   "fuel objects, UC params (min up/down, start cost) dropped"})

    for h in world.hydro_units:
        if not h.in_service:
            continue
        budget = h.reservoir_energy_mwh * hours / HOURS_PER_YEAR \
            if h.reservoir_energy_mwh > 0 else math.inf
        n.add("Generator", h.id, bus=h.bus_id, p_nom=h.p_max_mw,
              carrier="hydro", marginal_cost=1.0, e_sum_max=budget)
        cov.count("hydro_units")
        cov.approximated.append({
            "what": f"hydro budget ({h.id})",
            "how": "annual reservoir energy pro-rated to the export horizon "
                   f"({hours}h) as Generator.e_sum_max"})

    for s in world.storage_units:
        if not s.in_service:
            continue
        p = s.p_discharge_max_mw
        n.add("StorageUnit", s.id, bus=s.bus_id, p_nom=p,
              max_hours=(s.energy_capacity_mwh / p if p > 0 else 1.0),
              efficiency_store=s.efficiency_charge,
              efficiency_dispatch=s.efficiency_discharge,
              marginal_cost=s.vom_per_mwh, cyclic_state_of_charge=True)
        cov.count("storage_units")
        if abs(s.p_charge_max_mw - s.p_discharge_max_mw) > 1e-9:
            cov.approximated.append({
                "what": f"asymmetric charge/discharge power ({s.id})",
                "how": "PyPSA StorageUnit has one p_nom; discharge rating used"})
    if world.storage_units:
        cov.dropped.append({"what": "storage SOC bounds (soc_min_pu/soc_max_pu)",
                            "why": "no PyPSA StorageUnit equivalent"})

    for ld in world.loads:
        series = None
        if ld.demand_profile_id and ld.demand_profile_id in store:
            series = store.get(ld.demand_profile_id)[:hours] * world.demand_scale
        if series is None:
            series = np.zeros(hours)
            cov.invented.append({"what": f"demand for {ld.id}", "value": 0.0,
                                 "why": "no demand profile in the store"})
        n.add("Load", ld.id, bus=ld.bus_id,
              p_set=pd.Series(series, index=n.snapshots))
        cov.count("loads")
    cov.dropped.append({"what": "VOLL per load",
                        "why": "PyPSA has no native unserved-energy price"})

    for dc in world.dc_lines:
        n.add("Link", dc.id, bus0=dc.from_bus_id, bus1=dc.to_bus_id,
              p_nom=dc.p_max_mw, p_min_pu=-1.0)
        cov.count("dc_lines")

    for c in world.expansion_candidates:
        if c.kind.value == "generator":
            capex_annual = ((c.capex_per_mw or 0.0)
                            * _crf(0.07, c.lifetime_yr) + c.fom_per_mw_yr)
            n.add("Generator", c.id, bus=c.bus_id, p_nom=0.0,
                  p_nom_extendable=True, p_nom_max=c.build_max_mw or math.inf,
                  capital_cost=capex_annual, carrier=c.technology,
                  marginal_cost=c.vom_per_mwh)
            cov.count("expansion_candidates")
            cov.approximated.append({
                "what": f"candidate capex ({c.id})",
                "how": "overnight capex annualized at 7% over lifetime into "
                       "capital_cost; lifetime itself not representable"})
        elif c.kind.value == "line":
            capex_annual = ((c.capex_per_mw or 0.0)
                            * _crf(0.07, c.lifetime_yr) + c.fom_per_mw_yr)
            n.add("Link", c.id, bus0=c.from_bus_id, bus1=c.to_bus_id,
                  p_nom=0.0, p_nom_extendable=True, p_min_pu=-1.0,
                  p_nom_max=c.build_max_mw or math.inf,
                  capital_cost=capex_annual)
            cov.count("expansion_candidates")
        else:  # storage candidate: kernel sizes P and E independently
            payload.sidecar.park("glassbox:expansion_candidates", c.id,
                                 c.model_dump(mode="json"), "glassbox",
                                 "PyPSA StorageUnit cannot size power and "
                                 "energy independently")
            cov.parked.append({"concept": "glassbox:expansion_candidates",
                               "n": 1, "why": "independent P/E sizing"})

    # concepts with no PyPSA home at all: park them, restorable
    for coll in PARKED_COLLECTIONS:
        items = getattr(world, coll)
        for item in items:
            payload.sidecar.park(f"glassbox:{coll}", item.id,
                                 item.model_dump(mode="json"), "glassbox",
                                 "no PyPSA representation")
        if items:
            cov.parked.append({"concept": f"glassbox:{coll}", "n": len(items),
                               "why": "no PyPSA representation"})

    out = Payload("pypsa", n, sidecar=payload.sidecar,
                  coverage=list(payload.coverage))
    out.hop(cov)
    return out


# ---------------------------------------------------------------------------
# pypsa -> glassbox
# ---------------------------------------------------------------------------


def _map_carrier(carrier: str, cov: Coverage, gid: str) -> str:
    c = (carrier or "").strip().lower()
    if c in CARRIER_TO_GB:
        if c in AMBIGUOUS_CARRIERS:
            cov.approximated.append({
                "what": f"carrier '{carrier}' ({gid})",
                "how": f"ambiguous open label mapped to "
                       f"'{CARRIER_TO_GB[c]}' by convention"})
        return CARRIER_TO_GB[c]
    cov.manual_mapping_required.append({
        "entity": gid, "label": carrier,
        "note": "free-text carrier has no closed-enum home; defaulted to ccgt"})
    return "ccgt"


def _tile_to_year(arr: np.ndarray) -> np.ndarray:
    if len(arr) >= HOURS_PER_YEAR:
        return np.asarray(arr[:HOURS_PER_YEAR], dtype=float)
    reps = int(np.ceil(HOURS_PER_YEAR / max(len(arr), 1)))
    return np.tile(np.asarray(arr, dtype=float), reps)[:HOURS_PER_YEAR]


@bridge("pypsa", "glassbox",
        notes="free-text carriers -> closed enum (counted); restores glassbox:* sidecar")
def pypsa_to_glassbox(payload: Payload, opts: dict) -> Payload:
    from glassbox.schema import (ACLine, Bus, BusType, DCLine,
                                 ExpansionCandidate, Generator, GenTechnology,
                                 Hydro, Load, Storage, TimeSeries,
                                 TimeSeriesKind, Transformer, World, Zone)

    n = payload.native
    cov = Coverage(bridge="pypsa->glassbox")
    hours = max(len(n.snapshots), 1)
    world = World(id="translated", name="translated via rosetta",
                  base_power_mva=100.0)
    store = world.time_series_store
    sb = world.base_power_mva

    # coordinates: PyPSA models often carry none — invent a deterministic ring
    xs, ys = n.buses["x"].values, n.buses["y"].values
    invent_coords = bool(len(xs)) and float(np.abs(xs).max() + np.abs(ys).max()) == 0.0
    if invent_coords:
        cov.invented.append({"what": "bus coordinates", "value": "ring layout",
                             "why": "source has no geodata"})

    bus_ids = list(n.buses.index)
    for i, bid in enumerate(bus_ids):
        row = n.buses.loc[bid]
        if invent_coords:
            ang = 2 * math.pi * i / max(len(bus_ids), 1)
            bx, by = 100 * math.cos(ang), 100 * math.sin(ang)
        else:
            bx, by = float(row["x"]), float(row["y"])
        world.buses.append(Bus(id=str(bid), name=str(bid),
                               base_kv=float(row["v_nom"]) or 100.0,
                               zone_id="Z1", x=bx, y=by))
        cov.count("buses")

    total_load = 0.0
    if len(n.loads):
        static = n.loads["p_set"].fillna(0.0)
        for lid in n.loads.index:
            if len(n.loads_t.p_set.columns) and lid in n.loads_t.p_set.columns:
                total_load += float(n.loads_t.p_set[lid].max())
            else:
                total_load += float(static.loc[lid])

    for lid in n.lines.index:
        row = n.lines.loc[lid]
        vn = float(n.buses.loc[row["bus0"], "v_nom"]) or 100.0
        zbase = vn ** 2 / sb
        rating = float(row["s_nom"])
        if rating <= 0:
            rating = max(total_load * 2, 100.0)
            cov.invented.append({"what": f"line rating ({lid})", "value": rating,
                                 "why": "s_nom = 0 means unlimited in PyPSA; "
                                        "glassbox engines need a number"})
        world.ac_lines.append(ACLine(
            id=str(lid), name=str(lid), from_bus_id=str(row["bus0"]),
            to_bus_id=str(row["bus1"]),
            r=float(row["r"]) / zbase, x=max(float(row["x"]) / zbase, 1e-5),
            b=float(row.get("b", 0.0)) * zbase,
            rating_normal_mva=rating, rating_emergency_mva=rating * 1.2,
            rating_lt_mva=rating * 1.1))
        cov.count("ac_lines")

    for tid in n.transformers.index:
        row = n.transformers.loc[tid]
        s_nom = float(row["s_nom"]) or 100.0
        world.transformers.append(Transformer(
            id=str(tid), name=str(tid), from_bus_id=str(row["bus0"]),
            to_bus_id=str(row["bus1"]),
            x=max(float(row["x"]) * sb / s_nom, 1e-5), rating_mva=s_nom))
        cov.count("transformers")

    slack_bus = None
    for gid in n.generators.index:
        row = n.generators.loc[gid]
        carrier = str(row.get("carrier", "") or "")
        if str(row.get("control", "")) == "Slack" and slack_bus is None:
            slack_bus = str(row["bus"])
        if bool(row.get("p_nom_extendable", False)):
            capex_annual = float(row.get("capital_cost", 0.0))
            pmax = float(row.get("p_nom_max", math.inf))
            if not math.isfinite(pmax):
                pmax = max(total_load * 2, 100.0)
                cov.invented.append({"what": f"build ceiling ({gid})",
                                     "value": pmax,
                                     "why": "p_nom_max = inf; candidates need "
                                            "a finite build_max_mw"})
            tech = _map_carrier(carrier, cov, str(gid))
            world.expansion_candidates.append(ExpansionCandidate(
                id=str(gid), name=str(gid), kind="generator", technology=tech,
                bus_id=str(row["bus"]), build_max_mw=pmax,
                capex_per_mw=capex_annual / _crf(0.07, 30),
                vom_per_mwh=float(row.get("marginal_cost", 0.0))))
            cov.count("expansion_candidates")
            cov.approximated.append({
                "what": f"candidate capex ({gid})",
                "how": "annualized capital_cost de-annualized at assumed "
                       "7%/30yr (PyPSA carries no lifetime)"})
            continue
        if carrier.lower() == "hydro":
            e_sum = float(row.get("e_sum_max", math.inf))
            reservoir = (e_sum * HOURS_PER_YEAR / hours
                         if math.isfinite(e_sum) else 0.0)
            world.hydro_units.append(Hydro(
                id=str(gid), name=str(gid), bus_id=str(row["bus"]),
                p_max_mw=float(row["p_nom"]),
                reservoir_energy_mwh=reservoir))
            cov.count("hydro_units")
            continue
        tech = _map_carrier(carrier, cov, str(gid))
        avail_id = None
        if len(n.generators_t.p_max_pu.columns) and \
                gid in n.generators_t.p_max_pu.columns:
            arr = _tile_to_year(n.generators_t.p_max_pu[gid].values)
            avail_id = f"availability__{gid}"
            store.add(TimeSeries(id=avail_id,
                                 kind=TimeSeriesKind.AVAILABILITY), arr)
            if hours < HOURS_PER_YEAR:
                cov.approximated.append({
                    "what": f"availability series ({gid})",
                    "how": f"{hours}h horizon tiled to 8760h"})
        world.generators.append(Generator(
            id=str(gid), name=str(gid), bus_id=str(row["bus"]),
            technology=GenTechnology(tech),
            prime_mover="inverter" if tech in VRE_TECHS else "thermal",
            p_max_mw=float(row["p_nom"]),
            vom_per_mwh=float(row.get("marginal_cost", 0.0)),
            availability_profile_id=avail_id))
        cov.count("generators")
    if len(n.generators):
        cov.approximated.append({
            "what": "marginal cost decomposition",
            "how": "PyPSA marginal_cost carried as pure VOM; no fuel objects "
                   "or heat rates reconstructed"})

    for sid in n.storage_units.index:
        row = n.storage_units.loc[sid]
        p = float(row["p_nom"])
        world.storage_units.append(Storage(
            id=str(sid), name=str(sid), bus_id=str(row["bus"]),
            technology="battery",
            p_charge_max_mw=p, p_discharge_max_mw=p,
            energy_capacity_mwh=p * float(row.get("max_hours", 1.0)),
            efficiency_charge=float(row.get("efficiency_store", 0.95)),
            efficiency_discharge=float(row.get("efficiency_dispatch", 0.95)),
            vom_per_mwh=float(row.get("marginal_cost", 0.0))))
        cov.count("storage_units")
    if len(n.storage_units):
        cov.invented.append({"what": "storage technology class",
                             "value": "battery",
                             "why": "PyPSA StorageUnit carries no type"})

    for lid in n.links.index:
        row = n.links.loc[lid]
        if bool(row.get("p_nom_extendable", False)):
            pmax = float(row.get("p_nom_max", math.inf))
            if not math.isfinite(pmax):
                pmax = max(total_load, 100.0)
            world.expansion_candidates.append(ExpansionCandidate(
                id=str(lid), name=str(lid), kind="line",
                from_bus_id=str(row["bus0"]), to_bus_id=str(row["bus1"]),
                build_max_mw=pmax,
                capex_per_mw=float(row.get("capital_cost", 0.0)) / _crf(0.07, 40)))
            cov.count("expansion_candidates")
        else:
            world.dc_lines.append(DCLine(
                id=str(lid), name=str(lid), from_bus_id=str(row["bus0"]),
                to_bus_id=str(row["bus1"]), p_max_mw=float(row["p_nom"])))
            cov.count("dc_lines")
            if float(row.get("efficiency", 1.0)) != 1.0:
                cov.dropped.append({"what": f"link efficiency ({lid})",
                                    "why": "glassbox DCLine is lossless"})

    for lid in n.loads.index:
        row = n.loads.loc[lid]
        if len(n.loads_t.p_set.columns) and lid in n.loads_t.p_set.columns:
            arr = _tile_to_year(n.loads_t.p_set[lid].values)
            if hours < HOURS_PER_YEAR:
                cov.approximated.append({
                    "what": f"demand series ({lid})",
                    "how": f"{hours}h horizon tiled to 8760h"})
        else:
            arr = np.full(HOURS_PER_YEAR, float(row.get("p_set", 0.0)))
            cov.invented.append({"what": f"demand shape ({lid})",
                                 "value": "flat 8760h profile",
                                 "why": "source load is a static p_set"})
        pid = f"demand__{lid}"
        store.add(TimeSeries(id=pid, kind=TimeSeriesKind.DEMAND, unit="MW"), arr)
        world.loads.append(Load(id=str(lid), name=str(lid),
                                bus_id=str(row["bus"]),
                                demand_profile_id=pid))
        cov.count("loads")
    if len(n.loads):
        cov.invented.append({"what": "VOLL", "value": 10000.0,
                             "why": "PyPSA has no unserved-energy price; "
                                    "glassbox default applied"})

    if len(n.global_constraints):
        cov.dropped.append({"what": f"{len(n.global_constraints)} global "
                                    "constraint(s)",
                            "why": "not translated (schema-level CO2 caps "
                                   "would need fuel reconstruction)"})

    # ---- restore anything a previous leg parked for glassbox -------------
    restored_zone_ids: set[str] = set()
    for entry in payload.sidecar.take("glassbox:"):
        coll = entry.concept.split(":", 1)[1]
        from glassbox.schema import ENTITY_MODELS
        # collection -> entity model name mapping mirrors the API's convention
        name_by_coll = {
            "reserve_products": "ReserveProduct", "policies": "Policy",
            "interfaces": "Interface", "resource_potentials": "ResourcePotential",
            "zones": "Zone", "expansion_candidates": "ExpansionCandidate",
        }
        cls = ENTITY_MODELS[name_by_coll[coll]]
        item = cls.model_validate(entry.payload)
        getattr(world, coll).append(item)
        if coll == "zones":
            restored_zone_ids.add(item.id)
            for bid in item.member_bus_ids:
                for b in world.buses:
                    if b.id == bid:
                        b.zone_id = item.id
        cov.restored.append({"concept": entry.concept, "n": 1})
    if restored_zone_ids:
        world.zones = [z for z in world.zones if z.id != "Z1"]
    if not world.zones:
        world.zones.append(Zone(id="Z1", name="Zone 1",
                                member_bus_ids=[b.id for b in world.buses]))
        cov.invented.append({"what": "zonal structure", "value": "single zone",
                             "why": "PyPSA buses carry no zone membership"})

    ref = slack_bus or (world.buses[0].id if world.buses else "")
    world.reference_bus_id = ref
    for b in world.buses:
        if b.id == ref:
            b.bus_type = BusType.SLACK
    if slack_bus is None and world.buses:
        cov.invented.append({"what": "reference (slack) bus", "value": ref,
                             "why": "no Slack-control generator in source"})

    out = Payload("glassbox", world, sidecar=payload.sidecar,
                  coverage=list(payload.coverage))
    out.hop(cov)
    return out
