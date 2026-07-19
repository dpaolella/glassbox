"""Topology processing: node-breaker in, bus-branch out.

The EMS's first act after any switching operation, and the heart of the CIM
lesson: a power-flow bus (CIM ``TopologicalNode``) is **computed** — it is a
connected component of connectivity nodes across *closed* switches. Open a
bus-tie and one substation becomes two buses; open every switch around a bay
and its equipment is simply not part of the solvable network anymore.

``derive_bus_branch`` returns a bus-branch ``World`` the planning engines can
consume unchanged, plus the bookkeeping an EMS UI needs (which node is where,
what's isolated, what split). Invariant, CI-enforced: with every switch
closed the derived world IS the original object (identity fast-path), so the
substation layer is provably invisible to planning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import World

_BRANCH_COLLS = ["ac_lines", "transformers", "dc_lines"]
_INJECTION_COLLS = ["generators", "hydro_units", "storage_units", "loads", "shunts"]


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # deterministic root: lexicographically smallest id wins
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


@dataclass
class TopoNode:
    id: str                       # derived bus id (planning bus id, or bus__2…)
    substation_id: str
    planning_bus_id: str
    connectivity_nodes: list[str]
    energized: bool               # contains at least one busbar section


@dataclass
class DerivedTopology:
    world: World                  # bus-branch world for the engines
    identical: bool               # True => world IS the original object
    topo_nodes: list[TopoNode] = field(default_factory=list)
    node_of_cn: dict[str, str] = field(default_factory=dict)
    isolated_equipment: list[str] = field(default_factory=list)
    split_buses: dict[str, list[str]] = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "identical": self.identical,
            "n_topological_nodes": len([t for t in self.topo_nodes if t.energized]),
            "topo_nodes": [t.__dict__ for t in self.topo_nodes],
            "isolated_equipment": self.isolated_equipment,
            "split_buses": self.split_buses,
        }


def derive_bus_branch(world: World, force_rebuild: bool = False) -> DerivedTopology:
    if not world.substations:
        return DerivedTopology(world=world, identical=True)

    uf = _UnionFind([cn.id for cn in world.connectivity_nodes])
    for sw in world.switches:
        if not sw.open:
            uf.union(sw.from_node_id, sw.to_node_id)

    sub_by_id = {s.id: s for s in world.substations}
    busbar_cns = {bb.connectivity_node_id: bb for bb in world.busbar_sections}
    cns_of_sub: dict[str, list[str]] = {}
    for cn in world.connectivity_nodes:
        cns_of_sub.setdefault(cn.substation_id, []).append(cn.id)

    # components per substation (switches never cross substations)
    topo_nodes: list[TopoNode] = []
    node_of_cn: dict[str, str] = {}
    split_buses: dict[str, list[str]] = {}
    for sub_id, cn_ids in sorted(cns_of_sub.items()):
        comps: dict[str, list[str]] = {}
        for cid in sorted(cn_ids):
            comps.setdefault(uf.find(cid), []).append(cid)
        bus_id = sub_by_id[sub_id].bus_id
        energized = [c for c in comps.values() if any(x in busbar_cns for x in c)]
        dead = [c for c in comps.values() if not any(x in busbar_cns for x in c)]
        # deterministic naming: component with the lowest busbar section keeps
        # the planning bus id; later ones get __2, __3…
        energized.sort(key=lambda c: min(busbar_cns[x].section
                                         for x in c if x in busbar_cns))
        for k, comp in enumerate(energized):
            nid = bus_id if k == 0 else f"{bus_id}__{k + 1}"
            topo_nodes.append(TopoNode(nid, sub_id, bus_id, comp, True))
            for x in comp:
                node_of_cn[x] = nid
        if len(energized) > 1:
            split_buses[bus_id] = [t.id for t in topo_nodes
                                   if t.substation_id == sub_id and t.energized]
        for j, comp in enumerate(dead):
            nid = f"{bus_id}__dead{j + 1}"
            topo_nodes.append(TopoNode(nid, sub_id, bus_id, comp, False))
            for x in comp:
                node_of_cn[x] = nid

    energized_node = {t.id for t in topo_nodes if t.energized}

    # place every equipment terminal on its derived node
    term_nodes: dict[str, dict[int, str]] = {}
    for t in world.equipment_terminals:
        term_nodes.setdefault(t.equipment_id, {})[t.sequence] = \
            node_of_cn.get(t.connectivity_node_id, "")

    isolated: list[str] = []
    moves: dict[str, dict[int, str]] = {}   # equipment -> seq -> new bus id
    for eq_id, seqs in sorted(term_nodes.items()):
        alive = {seq: nid for seq, nid in seqs.items() if nid in energized_node}
        if len(alive) < len(seqs):
            isolated.append(eq_id)
        else:
            moves[eq_id] = alive

    identical = not split_buses and not isolated and all(
        len([t for t in topo_nodes if t.substation_id == s and t.energized]) == 1
        for s in cns_of_sub)
    if identical and not force_rebuild:
        return DerivedTopology(world=world, identical=True,
                               topo_nodes=topo_nodes, node_of_cn=node_of_cn)

    # ---- rebuild a bus-branch world for the engines ----------------------
    derived = world.model_copy()          # shallow: shares the series store
    iso = set(isolated)

    new_buses = list(world.buses)
    bus_by_id = {b.id: b for b in world.buses}
    for orig, ids in split_buses.items():
        for k, nid in enumerate(ids):
            if nid == orig:
                continue
            src = bus_by_id[orig]
            new_buses.append(src.model_copy(update={
                "id": nid, "name": f"{src.name or orig} (section {k + 1})",
                "x": src.x + 8.0 * k, "y": src.y + 8.0 * k}))
    derived.buses = new_buses

    derived.zones = [
        z.model_copy(update={"member_bus_ids": z.member_bus_ids + [
            nid for orig, ids in split_buses.items() if orig in z.member_bus_ids
            for nid in ids if nid != orig]})
        for z in world.zones]

    for coll in _BRANCH_COLLS:
        items = []
        for item in getattr(world, coll):
            upd = {}
            if item.id in iso:
                upd["in_service"] = False
            elif item.id in moves:
                m = moves[item.id]
                if 1 in m and m[1] != item.from_bus_id:
                    upd["from_bus_id"] = m[1]
                if 2 in m and m[2] != item.to_bus_id:
                    upd["to_bus_id"] = m[2]
            items.append(item.model_copy(update=upd) if upd else item)
        setattr(derived, coll, items)

    for coll in _INJECTION_COLLS:
        items = []
        for item in getattr(world, coll):
            if item.id in iso:
                # loads/shunts carry no in_service flag: an isolated injection
                # simply leaves the solvable network
                if "in_service" in type(item).model_fields:
                    items.append(item.model_copy(update={"in_service": False}))
                continue
            if item.id in moves and moves[item.id][1] != item.bus_id:
                items.append(item.model_copy(update={"bus_id": moves[item.id][1]}))
            else:
                items.append(item)
        setattr(derived, coll, items)

    # the derived world is bus-branch: engines must not see the layer twice
    derived.substations = []
    derived.voltage_levels = []
    derived.busbar_sections = []
    derived.connectivity_nodes = []
    derived.switches = []
    derived.equipment_terminals = []

    return DerivedTopology(world=derived, identical=False, topo_nodes=topo_nodes,
                           node_of_cn=node_of_cn, isolated_equipment=isolated,
                           split_buses=split_buses)
