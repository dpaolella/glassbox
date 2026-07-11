"""World service: load-or-build the world, and shared view helpers.

Backs the FastAPI app (Section 3.4). Holds one World in memory; loads the
serialized default world if present, otherwise builds it on first use so the app
runs on first launch (Section 8).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..operators import AttributeProjection
from ..schema import (
    ENTITY_MODELS,
    Facet,
    World,
    field_metadata,
)

DEFAULT_DATA_DIR = Path("data/default_world")

# Map World collection attribute -> the entity model type name (for metadata).
COLLECTION_MODELS: dict[str, str] = {
    "buses": "Bus",
    "zones": "Zone",
    "ac_lines": "ACLine",
    "transformers": "Transformer",
    "dc_lines": "DCLine",
    "shunts": "Shunt",
    "interfaces": "Interface",
    "generators": "Generator",
    "hydro_units": "Hydro",
    "storage_units": "Storage",
    "loads": "Load",
    "expansion_candidates": "ExpansionCandidate",
    "resource_potentials": "ResourcePotential",
    "fuels": "Fuel",
    "cost_curves": "CostCurve",
    "policies": "Policy",
    "reserve_products": "ReserveProduct",
    "system_constraints": "SystemConstraint",
    "disturbances": "Disturbance",
}


class WorldService:
    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR):
        self.data_dir = data_dir
        self._world: World | None = None

    @property
    def world(self) -> World:
        if self._world is None:
            self._world = self._load_or_build()
        return self._world

    def reset(self) -> None:
        """Drop the in-memory world (build-mode edits) and reload from disk."""
        self._world = None

    def _load_or_build(self) -> World:
        from ..world import build_default_world_with_weather, load_world

        if (self.data_dir / "world.json").exists():
            try:
                return load_world(self.data_dir)
            except Exception:  # pragma: no cover - fall back to fresh build
                pass
        world, _ = build_default_world_with_weather()
        return world

    # --- collections -----------------------------------------------------

    def collection(self, name: str) -> list:
        if name not in COLLECTION_MODELS:
            raise KeyError(name)
        return getattr(self.world, name)

    def find(self, name: str, entity_id: str):
        for item in self.collection(name):
            if item.id == entity_id:
                return item
        raise KeyError(entity_id)

    # --- per-unit conversion (Section 4.3) ------------------------------

    def per_unit_value(self, value: Any, unit: str | None, base: str | None,
                       device_mva_base: float | None = None,
                       bus_kv: float | None = None) -> dict | None:
        """Return a per-unit representation of a value where meaningful.

        Powers convert on the system MVA base; voltages on the bus kV base;
        machine-base per-unit reactances convert to system base. Returns None
        when no conversion applies.
        """
        if not isinstance(value, (int, float)) or unit is None:
            return None
        S = self.world.base_power_mva
        if unit in ("MW", "MVA", "MVAr"):
            return {"value": value / S, "unit": "pu", "note": f"on {S} MVA base"}
        if unit == "kV" and bus_kv:
            return {"value": value / bus_kv, "unit": "pu", "note": f"on {bus_kv} kV base"}
        if unit == "pu" and base == "machine_mva" and device_mva_base:
            from ..schema import convert_machine_to_system_base

            sys_pu = convert_machine_to_system_base(value, device_mva_base, S)
            return {"value": sys_pu, "unit": "pu",
                    "note": f"converted machine base {device_mva_base} MVA -> system {S} MVA"}
        if unit == "s" and base == "machine_mva" and device_mva_base:
            from ..schema import convert_inertia_to_system_base

            return {"value": convert_inertia_to_system_base(value, device_mva_base, S),
                    "unit": "s", "note": "inertia on system base"}
        return None

    def inspect_entity(self, collection: str, entity_id: str,
                       facet: str | None = None) -> dict:
        """Layer-filtered inspector payload for one entity (Section 9.2).

        Returns each in-scope field with its value, metadata, and (where
        applicable) its per-unit representation for the SI/pu toggle.
        """
        item = self.find(collection, entity_id)
        model_name = COLLECTION_MODELS[collection]
        meta = field_metadata(ENTITY_MODELS[model_name])

        if facet:
            scope = AttributeProjection(facet).fields_for(ENTITY_MODELS[model_name])
        else:
            scope = list(meta.keys())

        # context for conversions
        device_mva = getattr(item, "mva_base", None)
        bus_kv = None
        if hasattr(item, "bus_id"):
            try:
                bus_kv = self.world.bus(item.bus_id).base_kv
            except Exception:
                bus_kv = None
        if model_name == "Bus":
            bus_kv = getattr(item, "base_kv", None)

        fields = []
        for name in scope:
            if name not in meta:
                continue
            value = getattr(item, name, None)
            m = meta[name]
            pu = self.per_unit_value(value, m["unit"], m["base"], device_mva, bus_kv)
            fields.append({
                "name": name,
                "value": _jsonable(value),
                "unit": m["unit"],
                "base": m["base"],
                "facets": m["facets"],
                "description": m["description"],
                "per_unit": pu,
            })
        return {
            "collection": collection,
            "type": model_name,
            "id": entity_id,
            "facet": facet,
            "fields": fields,
            "attached": self._attached_for(collection, item),
        }

    def _attached_for(self, collection: str, item) -> list[dict]:
        """Navigable related entities: a bus lists its devices; a device links
        back to its bus. Powers click-through inspection (Section 9.1)."""
        w = self.world
        out: list[dict] = []
        if collection == "buses":
            bid = item.id
            for g in w.generators:
                if g.bus_id == bid:
                    out.append({"collection": "generators", "id": g.id,
                                "label": f"⚡ {g.id}", "kind": g.technology.value})
            for s in w.storage_units:
                if s.bus_id == bid:
                    out.append({"collection": "storage_units", "id": s.id,
                                "label": f"🔋 {s.id}", "kind": s.technology.value})
            for h in w.hydro_units:
                if h.bus_id == bid:
                    out.append({"collection": "hydro_units", "id": h.id,
                                "label": f"💧 {h.id}", "kind": h.technology.value})
            for ld in w.loads:
                if ld.bus_id == bid:
                    out.append({"collection": "loads", "id": ld.id,
                                "label": f"🏠 {ld.id}", "kind": "load"})
        elif hasattr(item, "bus_id") and item.bus_id:
            out.append({"collection": "buses", "id": item.bus_id,
                        "label": f"⬢ bus {item.bus_id}", "kind": "bus"})
        return out

    # --- network graph for react-flow (Section 9.1) ---------------------

    def graph(self) -> dict:
        w = self.world
        nodes = []
        for b in w.buses:
            attached = {
                "generators": [g.id for g in w.generators if g.bus_id == b.id],
                "loads": [ld.id for ld in w.loads if ld.bus_id == b.id],
                "storage": [s.id for s in w.storage_units if s.bus_id == b.id],
                "hydro": [h.id for h in w.hydro_units if h.bus_id == b.id],
            }
            nodes.append({
                "id": b.id, "name": b.name, "zone": b.zone_id,
                "x": b.x, "y": b.y, "base_kv": b.base_kv,
                "bus_type": b.bus_type.value, "attached": attached,
            })
        edges = []
        for ln in w.ac_lines:
            edges.append({"id": ln.id, "kind": "ac_line",
                          "from": ln.from_bus_id, "to": ln.to_bus_id,
                          "rating_mva": ln.rating_normal_mva, "x": ln.x})
        for tr in w.transformers:
            edges.append({"id": tr.id, "kind": "transformer",
                          "from": tr.from_bus_id, "to": tr.to_bus_id,
                          "rating_mva": tr.rating_mva})
        for dc in w.dc_lines:
            edges.append({"id": dc.id, "kind": "dc_line",
                          "from": dc.from_bus_id, "to": dc.to_bus_id,
                          "rating_mva": dc.p_max_mw})
        interfaces = [{"id": iface.id, "name": iface.name,
                       "member_line_ids": iface.member_line_ids,
                       "limit_mw": iface.limit_mw,
                       "limit_source": iface.limit_source.value}
                      for iface in w.interfaces]
        # Resource Potential layer: buildable candidates with siting + rough metrics
        bus_xy = {b.id: (b.x, b.y) for b in w.buses}
        candidates = []
        for c in w.expansion_candidates:
            site = c.bus_id or c.from_bus_id
            xy = bus_xy.get(site, (0.0, 0.0))
            candidates.append({
                "id": c.id, "name": c.name, "kind": c.kind.value,
                "technology": c.technology, "bus_id": c.bus_id,
                "from_bus_id": c.from_bus_id, "to_bus_id": c.to_bus_id,
                "x": xy[0], "y": xy[1],
                "build_max_mw": c.build_max_mw,
                "capex_per_mw": c.capex_per_mw,
                "lcoe_per_mwh": c.lcoe_per_mwh,
                "expected_capacity_factor": c.expected_capacity_factor,
            })
        # Resource Potential (zonal supply curves): one badge per curve, sited at
        # the zone hub, with its stepped tranches for the inspector / map glyph.
        resource_potentials = []
        for rp in w.resource_potentials:
            hub = rp.bus_id
            if not hub:
                zone = next((z for z in w.zones if z.id == rp.zone_id), None)
                hub = zone.member_bus_ids[0] if zone and zone.member_bus_ids else None
            xy = bus_xy.get(hub, (0.0, 0.0))
            tranches = [{
                "build_max_mw": t.build_max_mw, "capex_per_mw": t.capex_per_mw,
                "expected_capacity_factor": t.expected_capacity_factor,
                "lcoe_per_mwh": t.lcoe_per_mwh,
            } for t in rp.tranches]
            resource_potentials.append({
                "id": rp.id, "name": rp.name, "kind": rp.kind.value,
                "technology": rp.technology, "zone_id": rp.zone_id,
                "bus_id": hub, "x": xy[0], "y": xy[1],
                "total_build_max_mw": sum(t.build_max_mw for t in rp.tranches),
                "tranches": tranches,
            })
        return {"nodes": nodes, "edges": edges,
                "zones": [{"id": z.id, "name": z.name,
                           "member_bus_ids": z.member_bus_ids} for z in w.zones],
                "interfaces": interfaces, "candidates": candidates,
                "resource_potentials": resource_potentials,
                "terrain": self._terrain()}

    def _terrain(self) -> dict:
        """Procedural cartography (issue #26): a seeded landmass polygon,
        a river through the hydro zone, city markers sized by demand, and a
        resource field derived from the VRE weather sites. Deterministic from
        the world's weather seed; everything is derived, nothing is stored."""
        import math
        import random

        w = self.world
        seed = w.weather_model.seed if w.weather_model else 0
        rng = random.Random(seed * 104729 + 7)
        xs = [b.x for b in w.buses]
        ys = [b.y for b in w.buses]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        span = max(max(xs) - min(xs), max(ys) - min(ys))

        # landmass: a noisy radial blob comfortably containing every bus
        n_pts = 26
        land = []
        for i in range(n_pts):
            ang = 2 * math.pi * i / n_pts
            # base radius = farthest bus in this general direction + margin
            best = 0.0
            for b in w.buses:
                d = math.hypot(b.x - cx, b.y - cy)
                if d < 1e-9:
                    continue
                a = math.atan2(b.y - cy, b.x - cx)
                diff = abs((a - ang + math.pi) % (2 * math.pi) - math.pi)
                if diff < 0.9:
                    best = max(best, d * math.cos(diff))
            r = best + span * (0.16 + 0.07 * rng.random())
            land.append([cx + r * math.cos(ang), cy + r * math.sin(ang)])

        # river: rises past the hydro unit and flows off the north-west coast
        river = []
        hydro_bus = None
        if w.hydro_units:
            hydro_bus = next((b for b in w.buses
                              if b.id == w.hydro_units[0].bus_id), None)
        if hydro_bus:
            hx, hy = hydro_bus.x, hydro_bus.y
            pts = [(hx - span * 0.55, hy + span * 0.30),
                   (hx - span * 0.30, hy + span * 0.16),
                   (hx - span * 0.12, hy + span * 0.05),
                   (hx, hy),
                   (hx + span * 0.10, hy - span * 0.12),
                   (hx + span * 0.18, hy - span * 0.30)]
            river = [[px + rng.uniform(-8, 8), py + rng.uniform(-6, 6)]
                     for px, py in pts]

        # cities: load buses, radius scaled by their share of demand
        store = w.time_series_store
        demand = {}
        for ld in w.loads:
            if ld.demand_profile_id and ld.demand_profile_id in store:
                demand[ld.bus_id] = float(store.get(ld.demand_profile_id)[:8760].mean())
        dmax = max(demand.values(), default=1.0)
        bus_xy = {b.id: (b.x, b.y, b.name) for b in w.buses}
        cities = [{"bus_id": bid, "name": bus_xy[bid][2],
                   "x": bus_xy[bid][0], "y": bus_xy[bid][1],
                   "size": 0.35 + 0.65 * (d / dmax)}
                  for bid, d in demand.items() if bid in bus_xy]

        # resource field: one soft blob per VRE weather site, intensity from
        # the site's resource-quality scale (the same physics the CEM sees)
        blobs = []
        for site in w.weather_sites:
            if site.kind not in ("wind", "solar"):
                continue
            blobs.append({"kind": site.kind, "x": site.x, "y": site.y,
                          "r": span * 0.16,
                          "profile_id": f"availability__{site.id}",
                          "intensity": max(0.2, min(1.0, (site.scale or 1.0) - 0.2))})

        return {"land": land, "river": river, "cities": cities,
                "resource_blobs": blobs, "span": span}


def _jsonable(value: Any) -> Any:
    from enum import Enum

    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


service = WorldService()
