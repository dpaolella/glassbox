"""Polymorphic dynamic models (PRD Section 4.5.12).

The load-bearing structural decision of the whole tool (Section 1.3): one
abstract base, two concrete kinds, attached by Generator, Hydro, Storage, DC
Line and Shunt. The economic, adequacy and power-flow facets see a single P/Q
injection; the dynamics and EMT facets see a synchronous machine and a converter
as *fundamentally different objects*.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

from .facets import facet_field


class DynamicKind(str, Enum):
    SYNCHRONOUS = "synchronous"
    CONVERTER = "converter"


# --- nested controller blocks -------------------------------------------------


class GovernorModel(BaseModel):
    type: str = "TGOV1"
    droop_r: float = facet_field(facets=["dyn"], unit="pu", default=0.05,
                                 description="speed droop (pu)")
    time_constants: dict[str, float] = facet_field(
        facets=["dyn"], default_factory=dict,
        description="governor/turbine time constants (s)")
    p_min: float = facet_field(facets=["dyn"], unit="pu", default=0.0)
    p_max: float = facet_field(facets=["dyn"], unit="pu", default=1.0)


class ExciterModel(BaseModel):
    type: str = "IEEET1"
    gains: dict[str, float] = facet_field(facets=["dyn"], default_factory=dict)
    time_constants: dict[str, float] = facet_field(facets=["dyn"], default_factory=dict)
    v_ref: float = facet_field(facets=["dyn"], unit="pu", default=1.0)
    limits: dict[str, float] = facet_field(facets=["dyn"], default_factory=dict)


class PSSModel(BaseModel):
    type: str = "STAB1"
    gains: dict[str, float] = facet_field(facets=["dyn"], default_factory=dict)
    time_constants: dict[str, float] = facet_field(facets=["dyn"], default_factory=dict)


class TurbineModel(BaseModel):
    type: str = "steam"
    time_constants: dict[str, float] = facet_field(facets=["dyn"], default_factory=dict)


class SynchronousMachineModel(BaseModel):
    """Synchronous machine, dyn + emt facets (Section 4.5.12).

    Reactances are stored per-unit on the *machine's own MVA base*
    (``base="machine_mva"``); the dynamics engine converts to system base and
    surfaces the conversion in explain() (Section 4.3).
    """

    id: str = facet_field(facets=["core"])
    kind: Literal[DynamicKind.SYNCHRONOUS] = DynamicKind.SYNCHRONOUS

    h_s: float = facet_field(facets=["dyn"], unit="s", base="machine_mva", default=4.0,
                             description="inertia constant H, on machine base")
    damping_d: float = facet_field(facets=["dyn"], unit="pu", default=0.0,
                                   description="damping coefficient")

    # synchronous / transient / subtransient reactances (pu, machine base)
    xd: float = facet_field(facets=["dyn", "emt"], unit="pu", base="machine_mva", default=1.8)
    xq: float = facet_field(facets=["dyn", "emt"], unit="pu", base="machine_mva", default=1.7)
    xd_t: float = facet_field(facets=["dyn", "emt"], unit="pu", base="machine_mva", default=0.3,
                              description="d-axis transient reactance x'd")
    xq_t: float = facet_field(facets=["dyn", "emt"], unit="pu", base="machine_mva", default=0.55,
                              description="q-axis transient reactance x'q")
    xd_st: float = facet_field(facets=["dyn", "emt"], unit="pu", base="machine_mva", default=0.25,
                               description="d-axis subtransient reactance x''d")
    xq_st: float = facet_field(facets=["dyn", "emt"], unit="pu", base="machine_mva", default=0.25,
                               description="q-axis subtransient reactance x''q")

    # open-circuit time constants (s)
    td0_t: float = facet_field(facets=["dyn"], unit="s", default=8.0, description="T'd0")
    tq0_t: float = facet_field(facets=["dyn"], unit="s", default=0.4, description="T'q0")
    td0_st: float = facet_field(facets=["dyn"], unit="s", default=0.03, description="T''d0")
    tq0_st: float = facet_field(facets=["dyn"], unit="s", default=0.05, description="T''q0")

    saturation_params: dict[str, float] = facet_field(facets=["dyn"], default_factory=dict)

    governor: Optional[GovernorModel] = facet_field(facets=["dyn"], default=None)
    exciter: Optional[ExciterModel] = facet_field(facets=["dyn"], default=None)
    pss: Optional[PSSModel] = facet_field(facets=["dyn"], default=None)
    turbine: Optional[TurbineModel] = facet_field(facets=["dyn"], default=None)


# --- converter sub-blocks -----------------------------------------------------


class OuterLoop(BaseModel):
    p_gain: float = 0.5
    q_or_v_gain: float = 0.5
    time_constants: dict[str, float] = Field(default_factory=dict)


class InnerCurrentLoop(BaseModel):
    bandwidth_hz: float = 600.0
    gains: dict[str, float] = Field(default_factory=dict)


class FaultRideThrough(BaseModel):
    v_thresholds: dict[str, float] = Field(default_factory=dict)
    current_injection: dict[str, float] = Field(default_factory=dict)


class DCLink(BaseModel):
    capacitance: float = 0.02
    voltage_control: dict[str, float] = Field(default_factory=dict)


class LCLFilter(BaseModel):
    l1: float = Field(default=0.05, description="converter-side inductance (pu)")
    c: float = Field(default=0.05, description="filter capacitance (pu)")
    l2: float = Field(default=0.05, description="grid-side inductance (pu)")


class ConverterModel(BaseModel):
    """Inverter-based resource (IBR) control model, dyn + emt facets."""

    id: str = facet_field(facets=["core"])
    kind: Literal[DynamicKind.CONVERTER] = DynamicKind.CONVERTER

    control_mode: Literal["grid_following", "grid_forming"] = facet_field(
        facets=["dyn", "emt"], default="grid_following")
    current_limit_pu: float = facet_field(facets=["dyn", "emt"], unit="pu", default=1.2)

    # grid-following
    pll_bandwidth_hz: float = facet_field(facets=["dyn", "emt"], unit="Hz", default=20.0)
    outer_loop: OuterLoop = facet_field(facets=["dyn"], default_factory=OuterLoop)
    inner_current_loop: InnerCurrentLoop = facet_field(
        facets=["dyn", "emt"], default_factory=InnerCurrentLoop)

    # grid-forming
    droop_p_f: float = facet_field(facets=["dyn"], unit="pu", default=0.05,
                                   description="P-f droop")
    droop_q_v: float = facet_field(facets=["dyn"], unit="pu", default=0.05,
                                   description="Q-V droop")
    virtual_inertia_s: float = facet_field(facets=["dyn"], unit="s", default=0.0,
                                           description="virtual inertia (grid-forming)")

    # shared
    fault_ride_through: FaultRideThrough = facet_field(
        facets=["dyn", "emt"], default_factory=FaultRideThrough)
    dc_link: Optional[DCLink] = facet_field(facets=["dyn", "emt"], default=None)

    # emt only
    lcl_filter: Optional[LCLFilter] = facet_field(facets=["emt"], default=None)
    emt_switching_model: Literal["averaged", "switched"] = facet_field(
        facets=["emt"], default="averaged")


DynamicModel = Union[SynchronousMachineModel, ConverterModel]
"""Discriminated union; ``kind`` is the discriminator."""
