"""Switching operations under interlocks (Grid2Op's legality model, PRD §4).

Breakers interrupt load current: always operable. Disconnectors provide
visible isolation only: they may operate only *dead* — here, only when every
paired breaker recorded at elaboration time is open. A rejected operation
returns the reason as a teaching message instead of raising.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schema import World
from ..schema.substation import SwitchKind


@dataclass
class SwitchResult:
    applied: bool
    switch_id: str
    open: bool
    reason: str | None = None


def operate_switch(world: World, switch_id: str, open_: bool) -> SwitchResult:
    sw = next((s for s in world.switches if s.id == switch_id), None)
    if sw is None:
        return SwitchResult(False, switch_id, open_, f"no switch '{switch_id}'")
    if sw.open == open_:
        return SwitchResult(True, switch_id, open_, None)  # already there

    if sw.kind == SwitchKind.DISCONNECTOR and sw.paired_breaker_ids:
        by_id = {s.id: s for s in world.switches}
        closed = [b for b in sw.paired_breaker_ids
                  if b in by_id and not by_id[b].open]
        if closed:
            return SwitchResult(
                False, switch_id, open_,
                f"interlock: disconnector {switch_id} may only operate dead — "
                f"open breaker(s) {', '.join(closed)} first (a disconnector "
                "provides visible isolation; it cannot interrupt load current)")

    sw.open = open_
    return SwitchResult(True, switch_id, open_, None)


def reset_switches(world: World) -> int:
    """Return every switch to its as-drawn (normal) state. Returns count changed."""
    n = 0
    for sw in world.switches:
        if sw.open != sw.normal_open:
            sw.open = sw.normal_open
            n += 1
    return n
