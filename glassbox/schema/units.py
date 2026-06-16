"""Per-unit conversion as a *derived view*, never stored duplicate fields.

PRD Section 4.3: quantities are stored in SI with explicit named units. Per-unit
is computed on demand from the system base. The inspector toggles SI vs per-unit
(a Section 1.3 learning objective), and the machine-base vs system-base
distinction for synchronous-machine reactances is handled explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PerUnitSystem:
    """The per-unit base set, derived from the World's system base.

    A base voltage is required to form base impedance/current; voltage bases are
    per-bus (each bus has ``base_kv``), so impedance conversions are
    bus-specific. ``base_power_mva`` is system-wide.
    """

    base_power_mva: float
    base_frequency_hz: float

    def base_impedance_ohm(self, base_kv: float) -> float:
        """Z_base = (kV_base^2) / MVA_base  (line-to-line, three-phase)."""
        return (base_kv * base_kv) / self.base_power_mva

    def base_current_ka(self, base_kv: float) -> float:
        """I_base = MVA_base / (sqrt(3) * kV_base)  in kA."""
        return self.base_power_mva / (1.7320508075688772 * base_kv)

    def base_admittance_siemens(self, base_kv: float) -> float:
        return 1.0 / self.base_impedance_ohm(base_kv)

    # --- SI <-> per-unit for the common quantities -----------------------

    def power_to_pu(self, mw: float) -> float:
        return mw / self.base_power_mva

    def power_to_si(self, pu: float) -> float:
        return pu * self.base_power_mva

    def impedance_to_pu(self, ohm: float, base_kv: float) -> float:
        return ohm / self.base_impedance_ohm(base_kv)

    def impedance_to_si(self, pu: float, base_kv: float) -> float:
        return pu * self.base_impedance_ohm(base_kv)


def convert_machine_to_system_base(
    value_pu_machine: float,
    machine_mva: float,
    system_mva: float,
) -> float:
    """Convert a per-unit reactance/impedance from machine base to system base.

    Z_sys[pu] = Z_machine[pu] * (S_system / S_machine)

    PRD Sections 4.3 / 13.1: synchronous-machine reactances are conventionally
    given on the machine's own MVA base; the dynamics engine converts them to
    system base and surfaces the conversion in ``explain()``. This is a classic
    source of error and a good lesson, so the conversion lives in one auditable
    place.
    """
    if machine_mva <= 0:
        raise ValueError("machine_mva must be positive for base conversion")
    return value_pu_machine * (system_mva / machine_mva)


def convert_inertia_to_system_base(
    h_machine_s: float,
    machine_mva: float,
    system_mva: float,
) -> float:
    """Convert inertia constant H from machine base to system base.

    H_sys = H_machine * (S_machine / S_system)  (note: inverse direction of
    reactances, because H scales with the machine's own rating).
    """
    if system_mva <= 0:
        raise ValueError("system_mva must be positive for base conversion")
    return h_machine_s * (machine_mva / system_mva)
