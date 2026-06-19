"""Spatial projection operator (PRD Section 5.1).

``SpatialProjection(world, partition) -> SpatialView``

  * Identity (nodal layers): full bus-level topology.
  * Aggregate (zonal layers): collapse buses to zones via Zone.member_bus_ids.
    Zonal loads and generation sum exactly. Inter-zonal transfer limits are NOT
    a clean sum of line limits; we compute them with a stated, inspectable
    net-transfer-capacity estimate and surface in explain() that this coarsening
    is lossy and assumption-laden. This lossiness is a Section 1.3 lesson.
  * Elaborate (EMT): expands a positive-sequence node to three-phase; used only
    within EMT micro-examples (Section 6.6). Provided as a thin hook here.

The nodal-vs-zonal demonstration is: run the same world under identity and
aggregate, then diff (Section 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..explain import ExplainPayload, Formulation
from .base import Operator


class SpatialMode(str, Enum):
    IDENTITY = "identity"   # nodal
    AGGREGATE = "aggregate"  # zonal
    ELABORATE = "elaborate"  # three-phase (EMT)


@dataclass
class SpatialView:
    mode: SpatialMode
    node_ids: list[str]
    # mapping from original bus id -> node id (zone id when aggregated)
    bus_to_node: dict[str, str]
    # node -> list of original buses it represents
    node_members: dict[str, list[str]] = field(default_factory=dict)
    # inter-node transfer corridors: (from_node, to_node) -> limit_mw
    transfer_limits_mw: dict[tuple[str, str], float] = field(default_factory=dict)
    # internal (intra-node) branches that are collapsed away (lost detail)
    collapsed_branch_ids: list[str] = field(default_factory=list)
    # branches that cross node boundaries (retained, possibly bundled)
    crossing_branch_ids: list[str] = field(default_factory=list)


class SpatialProjection(Operator):
    name = "spatial"

    def __init__(self, mode: str | SpatialMode = SpatialMode.IDENTITY):
        self.mode = SpatialMode(mode)
        self._view: SpatialView | None = None
        self._stats: dict[str, Any] = {}

    def apply(self, world, **kwargs) -> SpatialView:
        if self.mode == SpatialMode.IDENTITY:
            view = self._identity(world)
        elif self.mode == SpatialMode.AGGREGATE:
            view = self._aggregate(world)
        else:
            view = self._elaborate(world)
        self._view = view
        return view

    # --- identity --------------------------------------------------------

    def _identity(self, world) -> SpatialView:
        node_ids = [b.id for b in world.buses]
        bus_to_node = {b.id: b.id for b in world.buses}
        self._stats = {"n_nodes": len(node_ids), "n_branches": len(world.branches)}
        return SpatialView(
            mode=SpatialMode.IDENTITY,
            node_ids=node_ids,
            bus_to_node=bus_to_node,
            node_members={b.id: [b.id] for b in world.buses},
            crossing_branch_ids=[br.id for br in world.branches],
        )

    # --- aggregate (zonal) ----------------------------------------------

    def _aggregate(self, world) -> SpatialView:
        bus_to_zone: dict[str, str] = {}
        node_members: dict[str, list[str]] = {}
        for z in world.zones:
            node_members[z.id] = list(z.member_bus_ids)
            for bid in z.member_bus_ids:
                bus_to_zone[bid] = z.id
        # buses not assigned to any zone fall back to their own zone_id field
        for b in world.buses:
            if b.id not in bus_to_zone:
                z = b.zone_id or b.id
                bus_to_zone[b.id] = z
                node_members.setdefault(z, []).append(b.id)

        node_ids = list(node_members.keys())

        collapsed: list[str] = []
        crossing: list[str] = []
        # net-transfer-capacity estimate: sum thermal ratings of the lines that
        # form each inter-zone cut (the stated, lossy method).
        corridor_lines: dict[tuple[str, str], list[float]] = {}
        for br in world.branches:
            fz = bus_to_zone.get(br.from_bus_id)
            tz = bus_to_zone.get(br.to_bus_id)
            if fz is None or tz is None:
                continue
            if fz == tz:
                collapsed.append(br.id)
                continue
            crossing.append(br.id)
            key = tuple(sorted((fz, tz)))
            rating = getattr(br, "rating_normal_mva", None) or getattr(br, "rating_mva", 0.0)
            corridor_lines.setdefault(key, []).append(float(rating))

        transfer_limits = {k: float(sum(v)) for k, v in corridor_lines.items()}

        self._stats = {
            "n_zones": len(node_ids),
            "n_buses_collapsed": sum(len(v) for v in node_members.values()),
            "n_intrazonal_branches_lost": len(collapsed),
            "n_interzonal_corridors": len(transfer_limits),
        }
        return SpatialView(
            mode=SpatialMode.AGGREGATE,
            node_ids=node_ids,
            bus_to_node=bus_to_zone,
            node_members=node_members,
            transfer_limits_mw=transfer_limits,
            collapsed_branch_ids=collapsed,
            crossing_branch_ids=crossing,
        )

    # --- elaborate (EMT three-phase) ------------------------------------

    def _elaborate(self, world) -> SpatialView:
        # Phase 0: structural hook only; full three-phase expansion lands with
        # the EMT engine (Phase 5, Section 6.6).
        identity = self._identity(world)
        identity.mode = SpatialMode.ELABORATE
        self._stats = {"note": "three-phase expansion deferred to EMT engine"}
        return identity

    # --- explain ---------------------------------------------------------

    def explain(self) -> ExplainPayload:
        if self.mode == SpatialMode.AGGREGATE:
            loss = [
                "Intra-zonal congestion is invisible: branches inside a zone are "
                "collapsed, so locational congestion and curtailment within the "
                "zone cannot appear (Section 1.3).",
                "Inter-zonal transfer limits are estimated by summing line "
                "thermal ratings on each cut (a net-transfer-capacity proxy). "
                "Real transfer capability is flow-based and lower; this estimate "
                "is assumption-laden and lossy.",
            ]
            symbolic = [
                "load[zone] = sum(load[bus] for bus in zone)",
                "gen[zone]  = sum(gen[bus] for bus in zone)",
                "NTC[z1,z2] = sum(rating[line] for line crossing z1<->z2)  # lossy",
            ]
            statement = ("Collapse buses to zones using Zone.member_bus_ids. "
                         "Loads and generation sum exactly; transfer limits do not.")
        elif self.mode == SpatialMode.IDENTITY:
            loss = ["None: identity returns the full nodal topology."]
            symbolic = ["view = world  (bus-level, lossless)"]
            statement = "Return the full bus-level topology unchanged."
        else:
            loss = ["Expands positive-sequence node to three phases (EMT only)."]
            symbolic = ["node -> {phase_a, phase_b, phase_c}"]
            statement = "Elaborate a node to three-phase for EMT micro-examples."

        return ExplainPayload(
            title=f"Spatial projection: {self.mode.value}",
            formulation=Formulation(statement=statement, symbolic=symbolic),
            inputs={"mode": self.mode.value},
            outputs=({"node_ids": self._view.node_ids,
                      "transfer_limits_mw": {f"{a}->{b}": v for (a, b), v in
                                             self._view.transfer_limits_mw.items()}}
                     if self._view else {}),
            intermediates=self._stats,
            information_loss=loss,
        )
