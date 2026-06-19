"""Engine ABC + explain() contract (PRD Sections 6.1, 2.2).

Every engine constructs its formulation *explicitly* so explain() can surface
it. Engines must not call an opaque end-to-end library function in solve()
(Section 6.1): the math is the lesson.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..explain import ExplainPayload

ENGINE_VERSION = "0.1.0"


class Engine(ABC):
    facets: list[str] = []
    name: str = "engine"

    @abstractmethod
    def build(self, view: Any) -> Any:
        """Construct the transparent formulation (constraints, equations)."""

    @abstractmethod
    def solve(self, model: Any) -> Any:
        """Solve and return a typed Result."""

    @abstractmethod
    def explain(self, model: Any, result: Any) -> ExplainPayload:
        """Return the structured transparency payload (Section 2.2)."""

    def run(self, view: Any) -> tuple[Any, ExplainPayload]:
        model = self.build(view)
        result = self.solve(model)
        return result, self.explain(model, result)
