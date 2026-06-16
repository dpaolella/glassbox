"""Facet (modeling-layer) tags and the field-metadata convention.

This module is the architectural keystone described in PRD Sections 2.5 and 4.2.
Every numeric/structural field in the schema is tagged with the modeling
layer(s) that *consume* it. The tagging is first-class and machine-readable so
that:

  * the attribute projection operator (Section 5.3) can return exactly the
    fields a layer reads, and
  * the layer-filtered inspector (Section 9.2) can show the same object at
    different depths per layer.

The delineation of abstraction levels is therefore a *structural property of the
schema*, not documentation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo


class Facet(str, Enum):
    """The modeling layers, coarsest physical identity to finest physics.

    See PRD Section 4.2.
    """

    CORE = "core"  # identity and topology, used by all layers
    INV = "inv"    # capacity expansion (investment)
    OPS = "ops"    # production cost (unit commitment + dispatch)
    ADQ = "adq"    # resource adequacy
    PF = "pf"      # steady-state power flow and contingency
    DYN = "dyn"    # RMS / phasor dynamics
    EMT = "emt"    # electromagnetic transients and resonance

    @classmethod
    def from_str(cls, value: "str | Facet") -> "Facet":
        if isinstance(value, Facet):
            return value
        return cls(value)


# Human-readable names for the inspector layer selector (Section 9.2).
FACET_LABELS: dict[Facet, str] = {
    Facet.CORE: "Identity & Topology",
    Facet.INV: "Capacity Expansion",
    Facet.OPS: "Operations (Production Cost)",
    Facet.ADQ: "Resource Adequacy",
    Facet.PF: "Power Flow & Contingency",
    Facet.DYN: "Stability (RMS Dynamics)",
    Facet.EMT: "Electromagnetic Transients",
}


def facet_field(
    *,
    facets: list[str | Facet],
    unit: str | None = None,
    base: str | None = None,
    default: Any = ...,
    default_factory: Any = None,
    description: str = "",
    **kwargs: Any,
) -> Any:
    """Wrap ``pydantic.Field`` carrying facet/unit/base metadata.

    See PRD Section 4.2. A field carrying ``facets=["ops", "dyn"]`` is consumed
    by both production cost and dynamics.

    ``unit`` declares the SI unit (Section 4.3). ``base`` records the per-unit
    base ("system_mva" or "machine_mva"); the machine-base vs system-base
    distinction is handled explicitly in the dynamics engine (Section 4.3).
    """
    normalized = [Facet.from_str(f).value for f in facets]
    extra = {"facets": normalized, "unit": unit, "base": base}
    if default_factory is not None:
        return Field(
            default_factory=default_factory,
            description=description,
            json_schema_extra=extra,
            **kwargs,
        )
    return Field(
        default,
        description=description,
        json_schema_extra=extra,
        **kwargs,
    )


def field_facets(info: FieldInfo) -> list[str]:
    """Return the facet codes attached to a pydantic FieldInfo (empty if none)."""
    extra = info.json_schema_extra
    if isinstance(extra, dict):
        facets = extra.get("facets")
        if isinstance(facets, list):
            return list(facets)
    return []


def field_unit(info: FieldInfo) -> str | None:
    extra = info.json_schema_extra
    if isinstance(extra, dict):
        return extra.get("unit")
    return None


def field_base(info: FieldInfo) -> str | None:
    extra = info.json_schema_extra
    if isinstance(extra, dict):
        return extra.get("base")
    return None


def fields_in_facet(model: type[BaseModel], facet: str | Facet) -> list[str]:
    """Names of fields on ``model`` that are in scope for ``facet``.

    This is the schema-introspection utility required by Section 4.2: "Provide a
    schema-introspection utility that returns, for any entity and any facet, the
    fields in scope." It powers the attribute projection operator (Section 5.3)
    and the inspector's layer filter (Section 9.2).
    """
    code = Facet.from_str(facet).value
    out: list[str] = []
    for name, info in model.model_fields.items():
        if code in field_facets(info):
            out.append(name)
    return out


def field_metadata(model: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """Full per-field metadata table for an entity model.

    Returns ``{field_name: {facets, unit, base, description, type}}``. The
    inspector and the API use this to render a faceted view of any entity
    without hard-coding field lists anywhere.
    """
    table: dict[str, dict[str, Any]] = {}
    for name, info in model.model_fields.items():
        annotation = info.annotation
        type_name = getattr(annotation, "__name__", str(annotation))
        table[name] = {
            "facets": field_facets(info),
            "unit": field_unit(info),
            "base": field_base(info),
            "description": info.description or "",
            "type": type_name,
        }
    return table


def all_facets_for(model: type[BaseModel]) -> list[str]:
    """Distinct facet codes that appear on any field of ``model``."""
    seen: list[str] = []
    for info in model.model_fields.values():
        for f in field_facets(info):
            if f not in seen:
                seen.append(f)
    # preserve canonical facet ordering
    order = [f.value for f in Facet]
    return [f for f in order if f in seen]
