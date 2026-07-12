"""World editing support (issue #28 v2): journal, patching, validation.

Every build-mode edit is recorded as a pair of data-described operation lists
(forward + inverse), so undo/redo replays plain data instead of trusting
closures — the journal itself is inspectable, like everything else here.
"""

from __future__ import annotations

from typing import Any

from ..schema import ENTITY_MODELS, World
from .service import COLLECTION_MODELS

# collections the editor may touch (world-structural things like buses/zones
# stay read-only — moving a bus would invalidate terrain, profiles, and ids)
EDITABLE_COLLECTIONS = {
    "generators", "storage_units", "hydro_units", "loads", "ac_lines",
    "expansion_candidates", "resource_potentials", "fuels", "policies",
    "interfaces", "reserve_products",
}

# fields that must never be edited in place
_PROTECTED_FIELDS = {"id"}


class EditError(ValueError):
    pass


def _find(world: World, collection: str, entity_id: str):
    for item in getattr(world, collection):
        if item.id == entity_id:
            return item
    raise EditError(f"no {collection[:-1]} '{entity_id}'")


def _validate_bus_refs(world: World, fields: dict[str, Any]) -> None:
    bus_ids = {b.id for b in world.buses}
    for key in ("bus_id", "from_bus_id", "to_bus_id"):
        if key in fields and fields[key] and fields[key] not in bus_ids:
            raise EditError(f"{key}='{fields[key]}' is not a bus in this world")


def apply_op(world: World, op: dict[str, Any]) -> None:
    """Apply one journal operation. Ops are plain data: add/remove/set."""
    coll = op["collection"]
    items = getattr(world, coll)
    if op["op"] == "add":
        model = ENTITY_MODELS[COLLECTION_MODELS[coll]]
        items.append(model.model_validate(op["entity"]))
    elif op["op"] == "remove":
        kept = [x for x in items if x.id != op["id"]]
        if len(kept) == len(items):
            raise EditError(f"no {coll[:-1]} '{op['id']}' to remove")
        setattr(world, coll, kept)
    elif op["op"] == "set":
        item = _find(world, coll, op["id"])
        patched = patch_entity_validated(world, coll, item, op["fields"])
        idx = items.index(item)
        items[idx] = patched
    else:  # pragma: no cover
        raise EditError(f"unknown op {op['op']}")


def patch_entity_validated(world: World, collection: str, item,
                           fields: dict[str, Any]):
    """Return a re-validated copy of ``item`` with ``fields`` applied.

    Pydantic re-validates the whole entity, so a bad type/enum/negative value
    is rejected with a real error instead of corrupting the world.
    """
    model = ENTITY_MODELS[COLLECTION_MODELS[collection]]
    unknown = set(fields) - set(model.model_fields)
    if unknown:
        raise EditError(f"unknown field(s) {sorted(unknown)} on {model.__name__}")
    protected = set(fields) & _PROTECTED_FIELDS
    if protected:
        raise EditError(f"field(s) {sorted(protected)} are read-only")
    _validate_bus_refs(world, fields)
    data = item.model_dump()
    data.update(fields)
    try:
        return model.model_validate(data)
    except Exception as exc:
        raise EditError(f"invalid value: {exc}") from exc


class EditJournal:
    """Undo/redo stacks of {label, forward, inverse} op lists."""

    MAX = 100

    def __init__(self) -> None:
        self.undo_stack: list[dict[str, Any]] = []
        self.redo_stack: list[dict[str, Any]] = []

    def record(self, label: str, forward: list[dict], inverse: list[dict]) -> None:
        self.undo_stack.append({"label": label, "forward": forward,
                                "inverse": inverse})
        del self.undo_stack[:-self.MAX]
        self.redo_stack.clear()  # a new edit invalidates the redo branch

    def undo(self, world: World) -> str:
        if not self.undo_stack:
            raise EditError("nothing to undo")
        entry = self.undo_stack.pop()
        for op in reversed(entry["inverse"]):
            apply_op(world, op)
        self.redo_stack.append(entry)
        return entry["label"]

    def redo(self, world: World) -> str:
        if not self.redo_stack:
            raise EditError("nothing to redo")
        entry = self.redo_stack.pop()
        for op in entry["forward"]:
            apply_op(world, op)
        self.undo_stack.append(entry)
        return entry["label"]

    def clear(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()

    def state(self) -> dict[str, Any]:
        return {
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
            "undo_label": self.undo_stack[-1]["label"] if self.undo_stack else None,
            "redo_label": self.redo_stack[-1]["label"] if self.redo_stack else None,
            "n_edits": len(self.undo_stack),
        }
