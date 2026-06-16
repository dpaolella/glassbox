"""Operator ABC + explain() contract (PRD Sections 5, 2.2).

Three operators turn the one stored world into the view a layer consumes. Each
implements ``explain()``: it must surface its own mapping and *where it loses
information*. Each operator can move in either direction depending on the target
layer (aggregate for economic layers, elaborate for EMT).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..explain import ExplainPayload


class Operator(ABC):
    name: str = "operator"

    @abstractmethod
    def apply(self, world, **kwargs) -> Any:
        """Produce the view for the target layer."""

    @abstractmethod
    def explain(self) -> ExplainPayload:
        """Surface the mapping and where it loses information (Section 2.2)."""
