"""The hub-and-spoke translation core.

Design commitment: rosetta has NO internal schema of its own. If it did, that
schema would just be a fourth hub contestant and the experiment would collapse.
Instead the tool is three pieces of *measurement machinery* wrapped around
pairwise bridges:

  * a **bridge registry** — a directed graph of (from_schema -> to_schema)
    translations; a "hub run" composes two bridges through the nominated hub;
  * a **sidecar protocol** — a standardized envelope for concepts the current
    leg's target cannot hold, so they can ride through a hub and be restored
    by a later leg that understands them;
  * a **coverage manifest** — every bridge must declare what it translated,
    what it approximated (and how), what it parked in the sidecar, what it
    dropped, and which defaults it invented. Silent loss is the enemy;
    the manifest turns "effectively lossless" from a slogan into a diff.

Comparing hubs H1 vs H2 for a task X -> Y is then literal: run X->H1->Y and
X->H2->Y and diff the merged manifests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# sidecar: concepts in transit that the current representation cannot hold
# ---------------------------------------------------------------------------


@dataclass
class SidecarEntry:
    """One parked concept.

    ``concept`` is namespaced by the schema that understands it natively, e.g.
    ``glassbox:reserve_products`` — a later bridge whose *target* understands
    the namespace may consume (restore) the entry; every other bridge must
    carry it through untouched.
    """

    concept: str                  # e.g. "glassbox:reserve_products"
    entity_id: str
    payload: dict                 # full serialized entity (restorable)
    source_schema: str
    reason: str                   # why it had to be parked


@dataclass
class Sidecar:
    entries: list[SidecarEntry] = field(default_factory=list)

    def park(self, concept: str, entity_id: str, payload: dict,
             source_schema: str, reason: str) -> None:
        self.entries.append(SidecarEntry(concept, entity_id, payload,
                                         source_schema, reason))

    def take(self, prefix: str) -> list[SidecarEntry]:
        """Remove and return entries whose concept starts with ``prefix``."""
        got = [e for e in self.entries if e.concept.startswith(prefix)]
        self.entries = [e for e in self.entries if not e.concept.startswith(prefix)]
        return got

    def to_json(self) -> list[dict]:
        return [e.__dict__ for e in self.entries]


# ---------------------------------------------------------------------------
# coverage: the honest ledger every bridge must keep
# ---------------------------------------------------------------------------


@dataclass
class Coverage:
    """What one bridge hop did to the model — nothing is allowed to be silent."""

    bridge: str
    translated: dict[str, int] = field(default_factory=dict)     # collection -> n
    approximated: list[dict] = field(default_factory=list)       # {what, how}
    parked: list[dict] = field(default_factory=list)             # {concept, n, why}
    restored: list[dict] = field(default_factory=list)           # {concept, n}
    dropped: list[dict] = field(default_factory=list)            # {what, why}
    invented: list[dict] = field(default_factory=list)           # {what, value, why}
    manual_mapping_required: list[dict] = field(default_factory=list)
    # entities whose type had to be mapped by hand or defaulted: the closed-
    # taxonomy cost the PyPSA-as-hub one-pager is about, counted per entity

    def count(self, collection: str, n: int = 1) -> None:
        self.translated[collection] = self.translated.get(collection, 0) + n

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# payload + bridge + registry
# ---------------------------------------------------------------------------


@dataclass
class Payload:
    """A model in some schema's native in-memory form, plus what it has shed."""

    schema: str
    native: Any
    sidecar: Sidecar = field(default_factory=Sidecar)
    coverage: list[Coverage] = field(default_factory=list)  # one per hop so far

    def hop(self, cov: Coverage) -> None:
        self.coverage.append(cov)


BridgeFn = Callable[[Payload, dict], Payload]


@dataclass
class Bridge:
    src: str
    dst: str
    fn: BridgeFn
    notes: str = ""


_REGISTRY: dict[tuple[str, str], Bridge] = {}


def bridge(src: str, dst: str, notes: str = ""):
    """Decorator registering a directed translation."""
    def wrap(fn: BridgeFn) -> BridgeFn:
        _REGISTRY[(src, dst)] = Bridge(src, dst, fn, notes)
        return fn
    return wrap


def bridges() -> dict[tuple[str, str], Bridge]:
    return dict(_REGISTRY)


def translate(payload: Payload, to: str, hub: Optional[str] = None,
              opts: Optional[dict] = None) -> Payload:
    """Translate ``payload`` to schema ``to``.

    With ``hub`` set, the route is forced through it (two hops) — even when a
    direct bridge exists — because measuring the hub is the point. Without a
    hub, a direct bridge is used.
    """
    opts = opts or {}
    src = payload.schema
    if src == to and hub is None:
        return payload
    if hub is None:
        b = _REGISTRY.get((src, to))
        if b is None:
            raise KeyError(f"no direct bridge {src} -> {to}; "
                           f"available: {sorted(_REGISTRY)}")
        return b.fn(payload, opts)
    legs = [(src, hub), (hub, to)] if src != hub else [(hub, to)]
    if to == hub:
        legs = [(src, hub)]
    for a, bname in legs:
        br = _REGISTRY.get((a, bname))
        if br is None:
            raise KeyError(f"no bridge {a} -> {bname} for hub route "
                           f"{src} -> {hub} -> {to}")
        payload = br.fn(payload, opts)
    return payload


def merged_manifest(payload: Payload) -> dict:
    """One dict summarizing every hop's coverage, for the compare-hubs diff."""
    out: dict[str, Any] = {"route": [c.bridge for c in payload.coverage], "hops": []}
    totals = {"approximated": 0, "parked": 0, "restored": 0, "dropped": 0,
              "invented": 0, "manual_mapping_required": 0}
    for cov in payload.coverage:
        out["hops"].append(cov.to_json())
        totals["approximated"] += len(cov.approximated)
        totals["parked"] += sum(p.get("n", 1) for p in cov.parked)
        totals["restored"] += sum(r.get("n", 1) for r in cov.restored)
        totals["dropped"] += len(cov.dropped)
        totals["invented"] += len(cov.invented)
        totals["manual_mapping_required"] += len(cov.manual_mapping_required)
    out["totals"] = totals
    out["sidecar_remaining"] = len(payload.sidecar.entries)
    return out
