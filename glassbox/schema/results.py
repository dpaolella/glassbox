"""Results schema — first-class and traceable (PRD 4.6, 4.7).

Every result carries provenance: the scenario, engine, version, and the input
field IDs that produced it, so any output number can be traced back through its
engine's ``explain()`` to the inputs and the binding constraints/equations
(Section 9.3/9.4). The realized capacity factor is the canonical end-to-end
example (Section 4.6/13.2).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    """Links a result back to its inputs and computation (Section 4.7)."""

    scenario_id: str = ""
    engine: str = ""
    engine_version: str = "0.1.0"
    input_field_ids: list[str] = Field(default_factory=list,
                                       description="schema field IDs consumed")
    governing: list[str] = Field(default_factory=list,
                                 description="binding constraints / governing equations")
    notes: str = ""


class ResultBase(BaseModel):
    scenario_id: str = ""
    engine: str = ""
    engine_version: str = "0.1.0"
    provenance: Provenance = Field(default_factory=Provenance)


class DispatchResult(ResultBase):
    """Per-generator/per-timestep dispatch (Section 4.6).

    ``realized_capacity_factor`` is derived = energy / (capacity * hours), with
    provenance linking to the availability profile and dispatch so the inspector
    can show the profile, the dispatch, and the curtailment gap (Section 9.4).
    """

    timesteps: list[int] = Field(default_factory=list)
    period_weights: list[float] = Field(default_factory=list)
    generation_mw: dict[str, list[float]] = Field(default_factory=dict)
    charge_mw: dict[str, list[float]] = Field(default_factory=dict)
    discharge_mw: dict[str, list[float]] = Field(default_factory=dict)
    soc_mwh: dict[str, list[float]] = Field(default_factory=dict)
    curtailment_mw: dict[str, list[float]] = Field(default_factory=dict)
    unserved_mw: dict[str, list[float]] = Field(default_factory=dict)
    realized_capacity_factor: dict[str, float] = Field(default_factory=dict)
    total_cost: float = 0.0


class NetworkResult(ResultBase):
    flow_mw: dict[str, float] = Field(default_factory=dict)
    nodal_price: dict[str, float] = Field(default_factory=dict)
    dual_values: dict[str, float] = Field(default_factory=dict)
    losses_mw: float = 0.0
    # per-timestep series for chronological playback (issue #27): signed flow
    # per line and $/MWh price per node, aligned with DispatchResult.timesteps
    flow_t_mw: dict[str, list[float]] = Field(default_factory=dict)
    nodal_price_t: dict[str, list[float]] = Field(default_factory=dict)


class CEMResult(ResultBase):
    built_capacity_mw: dict[str, float] = Field(default_factory=dict)
    built_storage_power_mw: dict[str, float] = Field(default_factory=dict)
    built_storage_energy_mwh: dict[str, float] = Field(default_factory=dict)
    built_transmission_mw: dict[str, float] = Field(default_factory=dict)
    # zonal resource-potential builds, aggregated over supply-curve tranches
    built_resource_potential_mw: dict[str, float] = Field(default_factory=dict)
    built_resource_potential_energy_mwh: dict[str, float] = Field(default_factory=dict)
    total_cost: float = 0.0
    cost_breakdown: dict[str, float] = Field(default_factory=dict)
    operational: Optional[DispatchResult] = None
    network: Optional[NetworkResult] = None


class PCMResult(ResultBase):
    dispatch: Optional[DispatchResult] = None
    network: Optional[NetworkResult] = None
    objective: float = 0.0
    solve_status: str = ""


class AdequacyResult(ResultBase):
    lole_hours_per_year: float = 0.0
    eue_mwh_per_year: float = 0.0
    loss_events: list[dict[str, Any]] = Field(default_factory=list)
    elcc_mw: dict[str, float] = Field(default_factory=dict)
    n_draws: int = 0


class PowerFlowResult(ResultBase):
    converged: bool = False
    iterations: int = 0
    bus_v_pu: dict[str, float] = Field(default_factory=dict)
    bus_angle_deg: dict[str, float] = Field(default_factory=dict)
    branch_flow_mw: dict[str, float] = Field(default_factory=dict)
    branch_flow_mvar: dict[str, float] = Field(default_factory=dict)
    losses_mw: float = 0.0
    convergence_trace: list[float] = Field(default_factory=list)
    contingency_violations: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class DynamicsResult(ResultBase):
    time_s: list[float] = Field(default_factory=list)
    states: dict[str, list[float]] = Field(default_factory=dict)
    frequency_nadir_hz: float = 0.0
    rocof_hz_per_s: float = 0.0
    damping_ratio: float = 0.0
    rotor_angle_separation_deg: float = 0.0
    voltage_recovery_s: float = 0.0


class ImpedanceScanResult(BaseModel):
    frequency_hz: list[float] = Field(default_factory=list)
    impedance_real: list[float] = Field(default_factory=list)
    impedance_imag: list[float] = Field(default_factory=list)
    resonance_peaks_hz: list[float] = Field(default_factory=list)
    short_circuit_ratio: float = 0.0


class EMTResult(ResultBase):
    time_s: list[float] = Field(default_factory=list)
    phase_a: dict[str, list[float]] = Field(default_factory=dict)
    phase_b: dict[str, list[float]] = Field(default_factory=dict)
    phase_c: dict[str, list[float]] = Field(default_factory=dict)
    impedance_scan: Optional[ImpedanceScanResult] = None
