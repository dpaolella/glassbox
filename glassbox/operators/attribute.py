"""Attribute projection operator (PRD Section 5.3).

``AttributeProjection(world, facet) -> fields in scope for that layer``

Reads the facet metadata (Section 4.2) and returns exactly the fields the layer
consumes, for every entity. This operator powers both the engines (each engine
requests its facet) and the inspector's layer filter (Section 9.2). It is the
structural form of "delineate the abstraction levels."
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..explain import ExplainPayload, Formulation
from ..schema import ENTITY_MODELS, Facet, fields_in_facet
from .base import Operator


class AttributeProjection(Operator):
    name = "attribute"

    def __init__(self, facet: str | Facet):
        self.facet = Facet.from_str(facet)
        self._scope: dict[str, list[str]] = {}

    def fields_for(self, model: type[BaseModel]) -> list[str]:
        """Field names of ``model`` in scope for this facet (always incl. id)."""
        names = fields_in_facet(model, self.facet)
        if "id" in model.model_fields and "id" not in names:
            names = ["id"] + names
        return names

    def project_entity(self, obj: BaseModel) -> dict[str, Any]:
        """Project a single entity instance down to its in-scope fields."""
        names = self.fields_for(type(obj))
        return {n: getattr(obj, n) for n in names if hasattr(obj, n)}

    def apply(self, world, **kwargs) -> dict[str, list[dict[str, Any]]]:
        """Project every entity collection in the world to in-scope fields.

        Returns ``{collection_name: [projected_dict, ...]}``. Collections with
        no fields in scope for this facet are dropped entirely — which is itself
        the lesson: the layer simply does not see them.
        """
        from ..schema import World  # local import to avoid cycle at module load

        assert isinstance(world, World)
        view: dict[str, list[dict[str, Any]]] = {}
        self._scope = {}
        for coll_name in World.ENTITY_COLLECTIONS:
            items = getattr(world, coll_name)
            if not items:
                continue
            model = type(items[0])
            names = self.fields_for(model)
            # If only identity (id) is in scope and facet != core, the layer
            # does not really consume this entity — keep identity for joins.
            self._scope[coll_name] = names
            view[coll_name] = [
                {n: getattr(it, n) for n in names if hasattr(it, n)} for it in items
            ]
        return view

    def scope_table(self) -> dict[str, list[str]]:
        """Per-entity in-scope field lists, computed from the schema directly.

        Usable without a World instance (drives the inspector's layer chips).
        """
        table: dict[str, list[str]] = {}
        for type_name, model in ENTITY_MODELS.items():
            table[type_name] = self.fields_for(model)
        return table

    def explain(self) -> ExplainPayload:
        scope = self._scope or self.scope_table()
        return ExplainPayload(
            title=f"Attribute projection -> facet '{self.facet.value}'",
            formulation=Formulation(
                statement=(
                    f"Select, for every entity, the fields tagged with facet "
                    f"'{self.facet.value}' in the schema metadata. The facet "
                    f"tags are first-class (Section 4.2); this operator is the "
                    f"structural form of delineating abstraction levels."),
                symbolic=[
                    "view[entity] = { f in entity.fields : facet in f.facets }",
                ],
            ),
            inputs={"facet": self.facet.value},
            outputs={"fields_in_scope": scope},
            intermediates={"n_entities_in_scope": len(scope)},
            information_loss=[
                "Fields tagged only for other facets are not visible at this "
                "layer; the layer is blind to them by construction.",
            ],
        )
