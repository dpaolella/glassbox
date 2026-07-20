"""The substation layer: a CIM-aligned node-breaker model (PRD issue #56, §9.0).

These entities mirror IEC 61970-301 / CGMES class names on purpose — the toy
schema is meant to be a legible miniature of the real exchange standard:

    Substation, VoltageLevel, BusbarSection, ConnectivityNode, Terminal,
    Switch (kind = breaker | disconnector, mirroring CIM's Breaker and
    Disconnector subclasses).

Two ideas carry all the pedagogy:

  * **The physical bus is equipment, not a node.** A `BusbarSection` is a
    conductor that occupies a `ConnectivityNode`; a power-flow "bus"
    (CIM `TopologicalNode`) is *computed* by collapsing connectivity nodes
    across closed switches — never stored. See ``glassbox/rtops/topology.py``.
  * **Structural state vs operating state.** ``Switch.normal_open`` is how the
    device is drawn (CIM EQ profile); ``Switch.open`` is how it is right now
    (CIM SSH profile). Two different files' worth of truth in CGMES, two
    fields here — the distinction is the lesson.

The layer is strictly additive: with every switch closed, topology processing
returns the original bus-branch world unchanged, and every planning engine is
provably unaffected (CI-enforced identity test).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from .facets import facet_field


class SwitchKind(str, Enum):
    """CIM's Breaker / Disconnector distinction, as a kind field.

    A breaker can interrupt load current; a disconnector only provides
    visible isolation and may operate only when dead or paralleled — the
    interlock in ``glassbox/rtops/switching.py`` enforces exactly that.
    """

    BREAKER = "breaker"
    DISCONNECTOR = "disconnector"


class SubstationArrangement(str, Enum):
    SINGLE_BUSBAR = "single_busbar"
    SPLIT_BUSBAR = "split_busbar"    # two sections + bus-tie breaker
    RING = "ring"


class Substation(BaseModel):
    """CIM ``Substation``: the container a planning bus elaborates into."""

    id: str = facet_field(facets=["core", "rtops"])
    name: str = facet_field(facets=["core"], default="")
    bus_id: str = facet_field(facets=["rtops"], default="",
                              description="planning bus this substation elaborates")
    arrangement: SubstationArrangement = facet_field(
        facets=["rtops"], default=SubstationArrangement.SINGLE_BUSBAR)


class VoltageLevel(BaseModel):
    """CIM ``VoltageLevel`` (with its ``BaseVoltage`` folded in as base_kv)."""

    id: str = facet_field(facets=["core", "rtops"])
    substation_id: str = facet_field(facets=["rtops"], default="")
    base_kv: float = facet_field(facets=["rtops"], unit="kV", default=100.0)


class ConnectivityNode(BaseModel):
    """CIM ``ConnectivityNode``: an authored connection point inside a
    substation. Terminals attach here; topology processing groups these into
    TopologicalNodes across closed switches."""

    id: str = facet_field(facets=["core", "rtops"])
    substation_id: str = facet_field(facets=["rtops"], default="")


class BusbarSection(BaseModel):
    """CIM ``BusbarSection``: the physical busbar — equipment occupying a
    connectivity node. A derived topological node is 'energized as a bus'
    precisely when it contains at least one busbar section."""

    id: str = facet_field(facets=["core", "rtops"])
    substation_id: str = facet_field(facets=["rtops"], default="")
    voltage_level_id: str = facet_field(facets=["rtops"], default="")
    connectivity_node_id: str = facet_field(facets=["rtops"], default="")
    section: int = facet_field(facets=["rtops"], default=1,
                               description="section number within the station "
                                           "(split-busbar arrangements)")


class Switch(BaseModel):
    """CIM ``Switch`` with kind = Breaker | Disconnector.

    ``normal_open`` is structural (EQ); ``open`` is the live operating state
    (SSH). ``paired_breaker_ids`` is the elaboration-time interlock hint: a
    disconnector may operate only when every paired breaker is open (dead /
    no-load operation) — see ``rtops/switching.py``.
    """

    id: str = facet_field(facets=["core", "rtops"])
    kind: SwitchKind = facet_field(facets=["rtops"], default=SwitchKind.BREAKER)
    substation_id: str = facet_field(facets=["rtops"], default="")
    from_node_id: str = facet_field(facets=["rtops"], default="")
    to_node_id: str = facet_field(facets=["rtops"], default="")
    normal_open: bool = facet_field(facets=["rtops"], default=False,
                                    description="as-drawn state (CIM EQ)")
    open: bool = facet_field(facets=["rtops"], default=False,
                             description="live operating state (CIM SSH)")
    paired_breaker_ids: list[str] = facet_field(
        facets=["rtops"], default_factory=list,
        description="breakers that must be open before this disconnector "
                    "may operate (empty for breakers)")
    bay_equipment_id: str = facet_field(
        facets=["rtops"], default="",
        description="equipment whose bay this switch belongs to (UI grouping)")


class EquipmentTerminal(BaseModel):
    """CIM ``Terminal``: attaches one end of a piece of equipment to a
    connectivity node. sequence 1 = from-side, 2 = to-side for branches;
    single-terminal equipment (generators, loads, storage) uses sequence 1.

    Kept as a separate entity (as in CIM) rather than fields on equipment, so
    existing planning entities are untouched by the substation layer."""

    id: str = facet_field(facets=["core", "rtops"])
    equipment_id: str = facet_field(facets=["rtops"], default="")
    sequence: int = facet_field(facets=["rtops"], default=1)
    connectivity_node_id: str = facet_field(facets=["rtops"], default="")


class OperatingArea(BaseModel):
    """The balancing area and its interconnection context (PRD §9.1).

    Planning has no "elsewhere"; operations is defined by it. The area's
    frequency bias B (MW/0.1 Hz, negative by NERC convention) plus the
    external system's bias and the tie capacity determine how stiff
    frequency feels and how far the area can lean on its neighbors —
    Reporting ACE = (NIa - NIs) - 10B(Fa - Fs) needs every field here.
    """

    id: str = facet_field(facets=["core", "rtops"])
    name: str = facet_field(facets=["core"], default="")
    frequency_bias_mw_per_0p1hz: float = facet_field(
        facets=["rtops"], unit="MW/0.1Hz", default=-80.0,
        description="the area's own bias B (negative by convention)")
    external_bias_mw_per_0p1hz: float = facet_field(
        facets=["rtops"], unit="MW/0.1Hz", default=-1500.0,
        description="rest-of-interconnection bias behind the ties")
    tie_capacity_mw: float = facet_field(
        facets=["rtops"], unit="MW", default=400.0,
        description="total transfer capability of the area's tie lines")
    scheduled_interchange_mw: list[float] = facet_field(
        facets=["rtops"], default_factory=list,
        description="hourly net scheduled interchange NIs (exports positive); "
                    "empty = zero schedule")
