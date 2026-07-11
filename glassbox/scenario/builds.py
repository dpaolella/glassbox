"""Materialize CEM build decisions back into a World (issue #9).

The central planning loop — decide what to build, then see how it operates —
requires the CEM's continuous build variables to become real assets that every
other layer (PCM, power flow, adequacy) can see. ``world_with_builds`` returns
a *new* world in which:

  * built generator candidates become ``Generator`` assets at their bus,
  * built storage candidates become ``Storage`` assets (power + energy sized
    independently, as decided),
  * built transmission corridors become ``ACLine``s with the candidate's
    reactance and the built MW as their rating,
  * built zonal supply-curve capacity becomes a ``Generator``/``Storage`` at
    the curve's interconnection hub, and
  * the consumed candidates / potentials are removed (a committed proposal is
    no longer a proposal).

Everything is annotated with a provenance note in ``name`` so the inspector
shows where the asset came from.
"""

from __future__ import annotations

from typing import Optional

from ..schema import (
    ACLine,
    CEMResult,
    Generator,
    GenTechnology,
    Storage,
    StorageTechnology,
    World,
)
from .runner import _clone_world


def _gen_tech(technology: str) -> GenTechnology:
    try:
        return GenTechnology(technology)
    except ValueError:
        return GenTechnology.CCGT


def _sto_tech(technology: str) -> StorageTechnology:
    try:
        return StorageTechnology(technology)
    except ValueError:
        return StorageTechnology.BATTERY


def _hub_bus(world: World, rp) -> Optional[str]:
    if rp.bus_id:
        return rp.bus_id
    zone = next((z for z in world.zones if z.id == rp.zone_id), None)
    return zone.member_bus_ids[0] if zone and zone.member_bus_ids else None


def world_with_builds(world: World, result: CEMResult,
                      min_mw: float = 1.0) -> World:
    """Return a new world with the CEM result's builds committed as assets."""
    w = _clone_world(world)

    cand_by_id = {c.id: c for c in w.expansion_candidates}
    rp_by_id = {rp.id: rp for rp in w.resource_potentials}
    consumed_cands: set[str] = set()
    consumed_rps: set[str] = set()

    # --- nodal candidate generators -> Generator assets ---
    for cid, mw in result.built_capacity_mw.items():
        c = cand_by_id.get(cid)
        if c is None or mw < min_mw:
            continue
        tech = _gen_tech(c.technology)
        is_vre = c.technology in ("wind", "solar_pv")
        w.generators.append(Generator(
            id=f"built_{cid}", name=f"{c.name} (built {mw:.0f} MW by CEM)",
            bus_id=c.bus_id or "", technology=tech, fuel_id=c.fuel_id,
            prime_mover="inverter" if is_vre else "thermal",
            p_max_mw=mw, p_min_pu=(0.0 if is_vre else c.p_min_pu),
            heat_rate_mmbtu_per_mwh=c.heat_rate_mmbtu_per_mwh,
            vom_per_mwh=c.vom_per_mwh, fom_per_mw_yr=c.fom_per_mw_yr,
            lifetime_yr=c.lifetime_yr,
            availability_profile_id=c.availability_profile_id))
        consumed_cands.add(cid)

    # --- nodal candidate storage -> Storage assets ---
    for cid, p_mw in result.built_storage_power_mw.items():
        c = cand_by_id.get(cid)
        if c is None or p_mw < min_mw:
            continue
        e_mwh = result.built_storage_energy_mwh.get(
            cid, p_mw * (c.duration_h or 4.0))
        w.storage_units.append(Storage(
            id=f"built_{cid}", name=f"{c.name} (built {p_mw:.0f} MW by CEM)",
            bus_id=c.bus_id or "", technology=_sto_tech(c.technology),
            p_charge_max_mw=p_mw, p_discharge_max_mw=p_mw,
            energy_capacity_mwh=e_mwh,
            efficiency_charge=c.efficiency_charge,
            efficiency_discharge=c.efficiency_discharge,
            vom_per_mwh=c.vom_per_mwh))
        consumed_cands.add(cid)

    # --- built corridors -> ACLines (real reactance-coupled lines now) ---
    for cid, mw in result.built_transmission_mw.items():
        c = cand_by_id.get(cid)
        if c is None or mw < min_mw:
            continue
        x = c.reactance_pu or 0.1
        w.ac_lines.append(ACLine(
            id=f"built_{cid}", name=f"{c.name} (built {mw:.0f} MW by CEM)",
            from_bus_id=c.from_bus_id or "", to_bus_id=c.to_bus_id or "",
            r=x / 10.0, x=x, b=0.02,
            rating_normal_mva=mw, rating_emergency_mva=mw * 1.2,
            rating_lt_mva=mw * 1.1))
        consumed_cands.add(cid)

    # --- zonal supply-curve builds -> assets at the curve's hub ---
    for rid, mw in result.built_resource_potential_mw.items():
        rp = rp_by_id.get(rid)
        if rp is None or mw < min_mw:
            continue
        hub = _hub_bus(w, rp)
        if hub is None:
            continue
        if rp.kind.value == "storage":
            e_mwh = result.built_resource_potential_energy_mwh.get(
                rid, mw * (rp.duration_h or 4.0))
            w.storage_units.append(Storage(
                id=f"built_{rid}", name=f"{rp.name} (built {mw:.0f} MW by CEM)",
                bus_id=hub, technology=_sto_tech(rp.technology),
                p_charge_max_mw=mw, p_discharge_max_mw=mw,
                energy_capacity_mwh=e_mwh,
                efficiency_charge=rp.efficiency_charge,
                efficiency_discharge=rp.efficiency_discharge,
                vom_per_mwh=rp.vom_per_mwh))
        else:
            is_vre = rp.technology in ("wind", "solar_pv")
            w.generators.append(Generator(
                id=f"built_{rid}", name=f"{rp.name} (built {mw:.0f} MW by CEM)",
                bus_id=hub, technology=_gen_tech(rp.technology),
                fuel_id=rp.fuel_id,
                prime_mover="inverter" if is_vre else "thermal",
                p_max_mw=mw, p_min_pu=(0.0 if is_vre else rp.p_min_pu),
                heat_rate_mmbtu_per_mwh=rp.heat_rate_mmbtu_per_mwh,
                vom_per_mwh=rp.vom_per_mwh, fom_per_mw_yr=rp.fom_per_mw_yr,
                lifetime_yr=rp.lifetime_yr,
                availability_profile_id=rp.availability_profile_id))
        consumed_rps.add(rid)

    # committed proposals are no longer proposals
    w.expansion_candidates = [c for c in w.expansion_candidates
                              if c.id not in consumed_cands]
    w.resource_potentials = [rp for rp in w.resource_potentials
                             if rp.id not in consumed_rps]
    return w
