"""Import/export foreign grid models via grid-rosetta (issue #53).

Glassbox imports rosetta **as a library** when translation is used — rosetta
stays glassbox-agnostic (glassbox is one spoke on its bench), and glassbox
depends on it as the optional ``translate`` extra:

    pip install "glassbox[translate]"

Everything here returns rosetta's fidelity artifacts alongside the model:
the coverage manifest (what the route translated / approximated / parked /
dropped / invented, and which entities need a human type mapping) and the
sidecar (concepts still in transit). The API layer surfaces both in the UI
at import time — a translated world arrives with its receipts, never
silently. The import sidecar is kept attached to the live world so a later
export can restore parked concepts on the way out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .schema import World


def _rosetta():
    try:
        import rosetta
    except ImportError as exc:  # pragma: no cover - message matters, not path
        raise RuntimeError(
            "grid-rosetta is not installed; translation is an optional "
            "feature — install it with: pip install 'glassbox[translate]'"
        ) from exc
    return rosetta


def availability() -> dict:
    """What the translation layer can do in this environment."""
    try:
        rosetta = _rosetta()
    except RuntimeError as exc:
        return {"available": False, "reason": str(exc)}
    from rosetta.schemas import _LOADERS
    from rosetta.solvers import solvers

    bridges = set(rosetta.bridges())
    hubs = sorted(({a for a, _ in bridges} & {b for _, b in bridges})
                  - {"glassbox"})
    direct_in = {a for a, b in bridges if b == "glassbox"}
    via_hub = {a for a, b in bridges
               if b in hubs and (b, "glassbox") in bridges}
    return {"available": True,
            "rosetta_version": getattr(rosetta, "__version__", "?"),
            "schemas": sorted(_LOADERS),
            "importable_from": sorted((direct_in | via_hub) - {"glassbox"}),
            "hubs": hubs,
            "bridges": sorted(f"{a}->{b}" for a, b in bridges),
            "solvers": sorted(solvers())}


@dataclass
class TranslationResult:
    world: Optional[World]        # None for exports
    manifest: dict                # per-hop coverage ledger + totals
    sidecar: Any                  # rosetta Sidecar (concepts in transit)
    out_dir: Optional[str] = None


def import_model(source: str | Any, schema: str, hub: Optional[str] = None,
                 hours: int = 168,
                 mapping: Optional[dict] = None) -> TranslationResult:
    """Translate a foreign model into a World, with its receipts.

    ``source`` is a path / builtin case name (handed to rosetta's loader for
    ``schema``) or an already-native object. ``mapping`` is an optional
    rosetta mapping dict resolving typeless entities onto closed taxonomies.
    """
    rosetta = _rosetta()
    opts: dict = {"hours": hours}
    if mapping:
        from rosetta.mapping import Mapping
        opts["mapping"] = Mapping.ensure(mapping)
    p = rosetta.load(schema, source)
    p = rosetta.translate(p, "glassbox", hub=hub, opts=opts)
    return TranslationResult(world=p.native, manifest=rosetta.manifest(p),
                             sidecar=p.sidecar)


def export_model(world: World, to_schema: str, out_dir: str | Path,
                 hub: Optional[str] = None, hours: int = 168,
                 sidecar: Any = None) -> TranslationResult:
    """Translate the World out to another schema and write it to disk.

    Pass the sidecar kept from a previous import so concepts glassbox could
    not hold (or that never left the sidecar) ride along and can be restored
    by a leg that understands them.
    """
    rosetta = _rosetta()
    p = rosetta.load("glassbox", world)
    if sidecar is not None:
        p.sidecar = sidecar
    p = rosetta.translate(p, to_schema, hub=hub, opts={"hours": hours})
    out = rosetta.dump(p, out_dir)
    return TranslationResult(world=None, manifest=rosetta.manifest(p),
                             sidecar=p.sidecar, out_dir=str(out))
