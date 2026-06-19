"""The transparency contract (PRD Section 2.2).

Every ``Engine`` and every ``Operator`` implements ``explain()`` returning an
``ExplainPayload``. The UI renders this payload (Section 9.3). An engine or
operator that cannot produce a faithful ``explain()`` is not complete (Section
2.2 / 13.1).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Formulation(BaseModel):
    """Human-readable + symbolic statement of the math.

    For an optimization: objective, constraints, variables. For power flow: the
    Jacobian and Newton step. For dynamics: the ODEs and integrator step.
    """

    statement: str = ""
    symbolic: list[str] = Field(default_factory=list,
                                description="objective/constraints/equations in symbolic form")
    variables: list[str] = Field(default_factory=list)


class ExplainPayload(BaseModel):
    """Structured transparency payload (Section 2.2)."""

    title: str = ""
    formulation: Formulation = Field(default_factory=Formulation)
    inputs: dict[str, Any] = Field(default_factory=dict,
                                   description="concrete values/arrays consumed, keyed by field ID")
    outputs: dict[str, Any] = Field(default_factory=dict)
    intermediates: dict[str, Any] = Field(default_factory=dict,
                                          description="duals, iteration trace, Jacobian, residuals")
    provenance: dict[str, Any] = Field(default_factory=dict,
                                       description="scenario ID, engine version, input field IDs")
    # operators specifically surface where information is lost (Section 5)
    information_loss: list[str] = Field(default_factory=list)
