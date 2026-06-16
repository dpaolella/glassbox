"""Phenomena checklist (PRD Sections 1.3, 11.3).

Structural success ("it ran") is necessary but not sufficient. For each layer the
phenomena listed in its engine subsection are acceptance tests. This module is
the machine-readable map from each learning objective / phenomenon to the
demonstration that proves it on the default system — a scenario preset and/or the
tests that encode it (Section 1.5 success criterion 4).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Phenomenon:
    layer: str
    name: str
    objective: str          # the Section 1.3 learning objective it serves
    preset_key: str | None  # Scenario Lab preset that demonstrates it
    test: str               # test that encodes it


PHENOMENA: list[Phenomenon] = [
    # Economic layers (6.2, 6.3)
    Phenomenon("cem", "Carbon price reshapes the capacity mix and cuts emissions",
               "system value / policy", "carbon_vs_none",
               "test_engines::test_carbon_price_reduces_fossil_generation"),
    Phenomenon("cem", "Storage power and energy are sized independently",
               "storage sizing", None,
               "test_engines::test_storage_power_and_energy_sized_independently"),
    Phenomenon("cem", "Realized capacity factor traces to the availability profile",
               "traceability", None,
               "test_engines::test_realized_capacity_factor_traces_to_availability"),
    Phenomenon("pcm", "Nodal reveals congestion and LMP spread zonal hides",
               "nodal vs zonal", "nodal_vs_zonal_pcm",
               "test_engines::test_nodal_reveals_congestion_zonal_hides_it"),
    Phenomenon("cem", "Curtailment differs nodal vs zonal",
               "nodal vs zonal", "nodal_vs_zonal_cem",
               "test_engines::test_nodal_curtailment_exceeds_zonal"),
    # Resource adequacy (6.4)
    Phenomenon("ra", "A single weather year understates tail risk",
               "many weather years", "ra_one_vs_many",
               "test_adequacy::test_single_year_understates_tail_risk"),
    Phenomenon("ra", "VRE ELCC is small and declines with penetration",
               "VRE value deflation", None,
               "test_adequacy::test_vre_elcc_declines_with_penetration"),
    Phenomenon("ra", "Storage ELCC depends on duration",
               "storage sizing", None,
               "test_adequacy::test_storage_elcc_increases_with_duration"),
    # Steady-state security (6.5)
    Phenomenon("pf", "AC power flow reveals losses the DC model omits",
               "economics vs physics", "pf_nodal_vs_zonal",
               "test_powerflow::test_dc_omits_losses_ac_reveals_them"),
    Phenomenon("pf", "An N-1 contingency causes a post-contingency overload",
               "security", "pf_nodal_vs_zonal",
               "test_powerflow::test_n1_contingency_causes_violations"),
    # Dynamics (6.6)
    Phenomenon("dyn", "Nadir and RoCoF worsen as inertia is displaced by inverters",
               "inertia / IBR vs synchronous", "dyn_inertia",
               "test_dynamics::test_low_inertia_deepens_nadir_on_default"),
    Phenomenon("dyn", "Fast frequency response arrests the frequency decline",
               "frequency response", "dyn_ffr",
               "test_dynamics::test_ffr_arrests_frequency_decline"),
    Phenomenon("dyn", "Grid-forming converters provide inertia grid-following do not",
               "IBR vs synchronous", None,
               "test_dynamics::test_grid_forming_provides_inertia"),
    Phenomenon("dyn", "A longer fault-clearing time breaks transient stability",
               "transient stability", None,
               "test_dynamics::test_longer_fault_clearing_breaks_stability"),
    Phenomenon("dyn", "A stability requirement flows up into operations/planning",
               "cross-layer wiring", "dyn_inertia",
               "test_dynamics::test_dynamics_to_operations_handoff"),
    # EMT (6.7)
    Phenomenon("emt", "A weak grid (low SCR) destabilizes a grid-following converter",
               "system strength / EMT", "emt_strong_vs_weak",
               "test_emt::test_emt_engine_demonstrates_weak_grid_instability"),
    Phenomenon("emt", "The impedance scan locates the resonance",
               "resonance", "emt_strong_vs_weak",
               "test_emt::test_impedance_scan_locates_lcl_resonance"),
    Phenomenon("emt", "The low-SCR screen selects the EMT pocket (RMS -> EMT)",
               "cross-layer wiring", None,
               "test_emt::test_rms_to_emt_handoff_selects_pocket"),
]


def phenomena_by_layer() -> dict[str, list[Phenomenon]]:
    out: dict[str, list[Phenomenon]] = {}
    for p in PHENOMENA:
        out.setdefault(p.layer, []).append(p)
    return out
