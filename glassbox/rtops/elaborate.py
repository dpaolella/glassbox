"""Substation elaboration: grow a node-breaker layer out of a bus-branch world.

Deterministic and idempotent. Every planning bus becomes a ``Substation``;
every piece of equipment attached to that bus gets a **bay** — a short chain
of switching devices between the busbar and the equipment terminal:

    busbar CN --[breaker]-- mid CN --[disconnector]-- equipment CN

so de-energizing follows real switching order: open the breaker (it can break
load current), then the disconnector (visible isolation, dead operation only —
the interlock in ``switching.py`` knows the pairing).

Arrangements (PRD open question 1b's proposal):
  * the busiest bus gets a **split busbar** — two sections joined by a bus-tie
    breaker, equipment alternating sections. Opening the tie splits the
    substation into two topological nodes: the scenario-6 mechanic.
  * the second-busiest gets a **ring bus** — corner nodes joined by ring
    breakers, one equipment bay per corner. Any single breaker can be opened
    without losing anything (the ring reroutes) — the arrangement's whole point.
  * everything else is single-busbar.
"""

from __future__ import annotations

from ..schema import World
from ..schema.substation import (BusbarSection, ConnectivityNode,
                                 EquipmentTerminal, Substation,
                                 SubstationArrangement, Switch, SwitchKind,
                                 VoltageLevel)

# (collection attr, bus field, terminal sequence)
_BRANCH_ENDS = [("ac_lines", "from_bus_id", 1), ("ac_lines", "to_bus_id", 2),
                ("transformers", "from_bus_id", 1), ("transformers", "to_bus_id", 2),
                ("dc_lines", "from_bus_id", 1), ("dc_lines", "to_bus_id", 2)]
_INJECTIONS = ["generators", "hydro_units", "storage_units", "loads", "shunts"]


def _attachments(world: World, bus_id: str) -> list[tuple[str, int]]:
    """(equipment_id, terminal sequence) attached at a bus, deterministic order."""
    out: list[tuple[str, int]] = []
    for coll, field, seq in _BRANCH_ENDS:
        for item in getattr(world, coll):
            if getattr(item, field) == bus_id:
                out.append((item.id, seq))
    for coll in _INJECTIONS:
        for item in getattr(world, coll):
            if item.bus_id == bus_id:
                out.append((item.id, 1))
    return out


def elaborate_world(world: World) -> World:
    """Add the substation layer in place (no-op if it already exists)."""
    if world.substations:
        return world

    ranked = sorted(world.buses,
                    key=lambda b: (-len(_attachments(world, b.id)), b.id))
    split_bus = ranked[0].id if ranked and len(_attachments(world, ranked[0].id)) >= 4 else None
    ring_bus = ranked[1].id if len(ranked) > 1 and len(_attachments(world, ranked[1].id)) >= 3 else None

    for bus in world.buses:
        attach = _attachments(world, bus.id)
        if bus.id == split_bus:
            arrangement = SubstationArrangement.SPLIT_BUSBAR
        elif bus.id == ring_bus:
            arrangement = SubstationArrangement.RING
        else:
            arrangement = SubstationArrangement.SINGLE_BUSBAR

        sub_id = f"{bus.id}__sub"
        world.substations.append(Substation(
            id=sub_id, name=f"{bus.name or bus.id} substation",
            bus_id=bus.id, arrangement=arrangement))
        vl_id = f"{bus.id}__vl"
        world.voltage_levels.append(VoltageLevel(
            id=vl_id, substation_id=sub_id, base_kv=bus.base_kv))

        def cn(node_id: str) -> str:
            world.connectivity_nodes.append(
                ConnectivityNode(id=node_id, substation_id=sub_id))
            return node_id

        def bay(eq_id: str, seq: int, busbar_cn: str) -> None:
            mid = cn(f"cn__{eq_id}__{seq}__mid")
            eq_cn = cn(f"cn__{eq_id}__{seq}")
            cb_id = f"cb__{eq_id}__{seq}"
            world.switches.append(Switch(
                id=cb_id, kind=SwitchKind.BREAKER, substation_id=sub_id,
                from_node_id=busbar_cn, to_node_id=mid, bay_equipment_id=eq_id))
            world.switches.append(Switch(
                id=f"ds__{eq_id}__{seq}", kind=SwitchKind.DISCONNECTOR,
                substation_id=sub_id, from_node_id=mid, to_node_id=eq_cn,
                paired_breaker_ids=[cb_id], bay_equipment_id=eq_id))
            world.equipment_terminals.append(EquipmentTerminal(
                id=f"t__{eq_id}__{seq}", equipment_id=eq_id, sequence=seq,
                connectivity_node_id=eq_cn))

        if arrangement == SubstationArrangement.SINGLE_BUSBAR:
            bus_cn = cn(f"cn__{bus.id}__bus1")
            world.busbar_sections.append(BusbarSection(
                id=f"{bus.id}__bb1", substation_id=sub_id, voltage_level_id=vl_id,
                connectivity_node_id=bus_cn, section=1))
            for eq_id, seq in attach:
                bay(eq_id, seq, bus_cn)

        elif arrangement == SubstationArrangement.SPLIT_BUSBAR:
            cn_a = cn(f"cn__{bus.id}__bus1")
            cn_b = cn(f"cn__{bus.id}__bus2")
            for k, node in ((1, cn_a), (2, cn_b)):
                world.busbar_sections.append(BusbarSection(
                    id=f"{bus.id}__bb{k}", substation_id=sub_id,
                    voltage_level_id=vl_id, connectivity_node_id=node, section=k))
            world.switches.append(Switch(
                id=f"cb__{bus.id}__tie", kind=SwitchKind.BREAKER,
                substation_id=sub_id, from_node_id=cn_a, to_node_id=cn_b))
            for i, (eq_id, seq) in enumerate(attach):
                bay(eq_id, seq, cn_a if i % 2 == 0 else cn_b)

        else:  # RING: one corner per equipment, breakers around the ring
            corners = [cn(f"cn__{bus.id}__ring{i + 1}")
                       for i in range(max(len(attach), 3))]
            ring_breakers: list[str] = []
            for i, corner in enumerate(corners):
                nxt = corners[(i + 1) % len(corners)]
                cb_id = f"cb__{bus.id}__ring{i + 1}"
                ring_breakers.append(cb_id)
                world.switches.append(Switch(
                    id=cb_id, kind=SwitchKind.BREAKER, substation_id=sub_id,
                    from_node_id=corner, to_node_id=nxt))
            # a corner "is" the busbar here: mark each corner as a section so
            # the topology processor treats the ring as energizable
            for i, corner in enumerate(corners):
                world.busbar_sections.append(BusbarSection(
                    id=f"{bus.id}__bb_ring{i + 1}", substation_id=sub_id,
                    voltage_level_id=vl_id, connectivity_node_id=corner,
                    section=i + 1))
            for i, (eq_id, seq) in enumerate(attach):
                corner = corners[i]
                eq_cn = cn(f"cn__{eq_id}__{seq}")
                # adjacent ring breakers must be open before this DS may move
                adj = [ring_breakers[i], ring_breakers[i - 1]]
                world.switches.append(Switch(
                    id=f"ds__{eq_id}__{seq}", kind=SwitchKind.DISCONNECTOR,
                    substation_id=sub_id, from_node_id=corner, to_node_id=eq_cn,
                    paired_breaker_ids=sorted(set(adj)), bay_equipment_id=eq_id))
                world.equipment_terminals.append(EquipmentTerminal(
                    id=f"t__{eq_id}__{seq}", equipment_id=eq_id, sequence=seq,
                    connectivity_node_id=eq_cn))

    return world
