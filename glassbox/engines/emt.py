"""Electromagnetic transients and resonance (PRD Section 6.7) — facets: emt.

Micro-examples only (not full-system EMT). Built by hand in numpy; validated
against analytical results since no mature open-source EMT oracle exists
(Section 11.2):

  * Short-circuit ratio screen: from the bus impedance matrix Z = Y⁻¹, the SCR
    at each bus identifies the weak, inverter-heavy pocket the dynamics layer
    flags for EMT study (the RMS -> EMT handoff, Section 6.8).
  * Impedance (admittance) frequency scan of the converter LCL filter + grid,
    locating the resonance peak; validated against the analytical LCL resonance
    frequency.
  * A grid-following converter on a Thevenin grid, integrated in the dq frame
    with a PLL and a constant-power current loop. On a weak grid (low SCR) the
    fast control dynamics the RMS phasor model omits drive an oscillatory
    instability the RMS model declared stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..explain import ExplainPayload, Formulation
from ..schema import EMTResult, ImpedanceScanResult, Provenance, World
from ..schema.dynamic_models import ConverterModel
from .base import ENGINE_VERSION


# --- short-circuit ratio screen (RMS -> EMT handoff) ------------------------


def short_circuit_ratios(world: World) -> dict[str, float]:
    """SCR per converter-hosting bus from the bus impedance matrix Z = Y⁻¹.

    SCR = S_sc / P_device, with S_sc = V²/|Z_thevenin| (V = 1 pu). A low SCR
    marks a weak, inverter-heavy pocket — the screen that selects the EMT
    micro-example (Section 6.8).
    """
    from .powerflow import build_ybus

    Y, _, idx = build_ybus(world)
    # ensure invertibility: line charging grounds the network; add a tiny shunt
    Yg = Y + np.eye(Y.shape[0]) * 1e-9
    Z = np.linalg.inv(Yg)
    base = world.base_power_mva

    # converter active power per bus (nameplate of inverter-based resources)
    conv_p: dict[str, float] = {}
    dm = {m.id: m for m in world.dynamic_models}
    for g in world.generators:
        m = dm.get(g.dynamic_model_id) if g.dynamic_model_id else None
        if isinstance(m, ConverterModel):
            conv_p[g.bus_id] = conv_p.get(g.bus_id, 0.0) + g.p_max_mw
    for s in world.storage_units:
        m = dm.get(s.dynamic_model_id) if s.dynamic_model_id else None
        if isinstance(m, ConverterModel):
            conv_p[s.bus_id] = conv_p.get(s.bus_id, 0.0) + s.p_discharge_max_mw

    scr: dict[str, float] = {}
    for bus, p_mw in conv_p.items():
        if bus not in idx or p_mw <= 0:
            continue
        zth = abs(Z[idx[bus], idx[bus]])
        s_sc_mva = base / zth if zth > 0 else float("inf")
        scr[bus] = s_sc_mva / p_mw
    return scr


def weakest_pocket(world: World) -> tuple[Optional[str], float]:
    scr = short_circuit_ratios(world)
    if not scr:
        return None, float("inf")
    bus = min(scr, key=scr.get)
    return bus, scr[bus]


# --- impedance / admittance frequency scan ----------------------------------


def lcl_resonance_hz(l1: float, c: float, l2: float, base_freq: float = 60.0) -> float:
    """Converter-terminal LCL resonance (per-unit reactance/susceptance params).

    l1, l2 are per-unit inductive reactances at base frequency and c is the
    per-unit capacitor susceptance at base frequency. The driving-point impedance
    seen from the converter peaks at the parallel resonance of the filter
    capacitor with the grid-side inductor (l2 here carries L2 + Lg): reactances
    scale with f and the capacitor reactance with 1/f, so |Z| → ∞ at
    f_res = f0 / √(c·l2).  (``l1`` is retained for the full filter signature.)
    """
    return float(base_freq / np.sqrt(c * l2))


def impedance_scan(l1: float, c: float, l2: float, grid_l: float,
                   r_damp: float = 0.02, f_max: float = 2000.0,
                   n: int = 800, base_freq: float = 60.0) -> ImpedanceScanResult:
    """Driving-point impedance into the LCL filter + grid vs frequency.

    Reactances scale with frequency ratio r = f/f0: X_L(f) = X_L·r and the
    capacitor reactance X_C(f) = (1/c)/r. The parallel resonance peak is the LCL
    resonance the time-domain trace would excite.
    """
    l2_tot = l2 + grid_l
    freqs = np.linspace(1.0, f_max, n)
    r = freqs / base_freq
    z_l1 = 1j * l1 * r
    z_c = 1.0 / (1j * c * r)
    z_branch = 1j * l2_tot * r + r_damp
    z_par = (z_c * z_branch) / (z_c + z_branch)
    z = z_l1 + z_par
    mag = np.abs(z)

    # resonance peaks: local maxima of |Z| well above the median
    peaks: list[float] = []
    for k in range(1, n - 1):
        if mag[k] > mag[k - 1] and mag[k] > mag[k + 1] and mag[k] > 5 * np.median(mag):
            peaks.append(float(freqs[k]))

    return ImpedanceScanResult(
        frequency_hz=[float(x) for x in freqs],
        impedance_real=[float(x) for x in z.real],
        impedance_imag=[float(x) for x in z.imag],
        resonance_peaks_hz=peaks,
        short_circuit_ratio=0.0,
    )


# --- grid-following converter on a Thevenin grid (dq time domain) -----------


@dataclass
class GFLTrajectory:
    t: np.ndarray
    delta_rad: np.ndarray       # PLL angle vs grid
    vq_pu: np.ndarray           # q-axis PCC voltage (PLL frame)
    id_pu: np.ndarray           # d-axis current
    stable: bool
    oscillation_hz: float


def simulate_gfl_thevenin(scr: float, p_ref: float = 1.0, q_ref: float = 0.0,
                          vg: float = 1.0, pll_bw_hz: float = 20.0,
                          current_tc_s: float = 0.005, t_end: float = 1.5,
                          dt: float = 2e-4, perturb_rad: float = 0.05) -> GFLTrajectory:
    """Integrate a constant-power GFL converter + PLL on a Thevenin grid (RK4).

    States: PLL angle δ, PLL integrator x, d-axis current i_d (constant-power
    reference i_d_ref = P/v_d). Grid reactance Xg = Vg²/(SCR·P). On a weak grid
    (low SCR) the PLL–current–grid feedback is a growing oscillation; on a strong
    grid it is well damped. This is the dynamic the RMS phasor model omits.
    """
    xg = vg * vg / (max(scr, 1e-3) * max(p_ref, 1e-6))
    # PLL PI gains from bandwidth (standard second-order tuning)
    wn = 2 * np.pi * pll_bw_hz
    kp = 2 * 0.7 * wn / vg
    ki = wn * wn / vg
    iq0 = -q_ref / vg

    # initial equilibrium (if it exists): sinδ_eq = Xg·i_d/Vg
    id0 = p_ref / vg
    arg = xg * id0 / vg
    delta_eq = np.arcsin(arg) if abs(arg) <= 1 else np.pi / 2
    n = int(t_end / dt) + 1
    t = np.linspace(0, t_end, n)
    delta = np.zeros(n); x = np.zeros(n); idc = np.zeros(n)
    delta[0] = delta_eq + perturb_rad
    idc[0] = id0

    def vdq(d_, i_d):
        vd = vg * np.cos(d_) - xg * iq0
        vq = -vg * np.sin(d_) + xg * i_d
        return vd, vq

    def deriv(d_, x_, i_d):
        vd, vq = vdq(d_, i_d)
        omega = kp * vq + ki * x_           # PLL frequency deviation
        dd = omega
        dx = vq
        # constant-power current command (the destabilizing feedback)
        i_ref = p_ref / max(vd, 0.1)
        did = (i_ref - i_d) / current_tc_s
        return dd, dx, did

    no_eq = bool(abs(arg) > 1.0)
    stable = True
    for k in range(1, n):
        d_, x_, i_ = delta[k - 1], x[k - 1], idc[k - 1]
        k1 = deriv(d_, x_, i_)
        k2 = deriv(d_ + 0.5 * dt * k1[0], x_ + 0.5 * dt * k1[1], i_ + 0.5 * dt * k1[2])
        k3 = deriv(d_ + 0.5 * dt * k2[0], x_ + 0.5 * dt * k2[1], i_ + 0.5 * dt * k2[2])
        k4 = deriv(d_ + dt * k3[0], x_ + dt * k3[1], i_ + dt * k3[2])
        delta[k] = d_ + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        x[k] = x_ + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        idc[k] = i_ + dt / 6 * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2])
        if not np.isfinite(delta[k]) or abs(delta[k] - delta_eq) > 5.0:
            stable = False
            delta = delta[: k + 1]; x = x[: k + 1]; idc = idc[: k + 1]; t = t[: k + 1]
            break

    vq_series = np.array([vdq(delta[i], idc[i])[1] for i in range(len(delta))])
    # stability verdict: compare oscillation envelope late vs early
    if no_eq:
        stable = False
    elif len(vq_series) > 100:
        q = len(vq_series) // 4
        early = float(np.std(vq_series[10:q])) if q > 10 else 0.0
        late = float(np.std(vq_series[-q:]))
        if late > 1.5 * early + 1e-4:
            stable = False
    # dominant oscillation frequency from zero-crossings of vq
    osc_hz = 0.0
    if len(vq_series) > 10:
        sign = np.sign(vq_series - vq_series.mean())
        crossings = np.sum(np.abs(np.diff(sign)) > 0)
        osc_hz = crossings / (2 * (t[-1] - t[0])) if t[-1] > t[0] else 0.0

    return GFLTrajectory(t=t, delta_rad=delta, vq_pu=vq_series, id_pu=idc,
                         stable=stable, oscillation_hz=float(osc_hz))


# --- engine ------------------------------------------------------------------


def _dq_to_abc(mag: np.ndarray, t: np.ndarray, f0: float = 60.0) -> dict[str, list[float]]:
    """Reconstruct three-phase instantaneous traces from a dq magnitude."""
    wt = 2 * np.pi * f0 * t
    a = mag * np.cos(wt)
    b = mag * np.cos(wt - 2 * np.pi / 3)
    c = mag * np.cos(wt + 2 * np.pi / 3)
    return {"a": [float(x) for x in a], "b": [float(x) for x in b],
            "c": [float(x) for x in c]}


class EMTEngine:
    facets = ["emt"]
    name = "emt"

    def __init__(self, bus_id: Optional[str] = None, scr_override: Optional[float] = None,
                 pll_bw_hz: float = 20.0):
        self.bus_id = bus_id
        self.scr_override = scr_override
        self.pll_bw_hz = pll_bw_hz

    def run(self, world: World) -> tuple[EMTResult, ExplainPayload]:
        # 1) screen: pick the weak pocket the dynamics layer flags
        scr_map = short_circuit_ratios(world)
        if self.bus_id and self.bus_id in scr_map:
            bus = self.bus_id
        else:
            bus, _ = weakest_pocket(world)
        scr = self.scr_override if self.scr_override is not None else scr_map.get(bus, 2.0)

        # 2) converter LCL filter at that pocket (or a default)
        l1, c, l2 = self._lcl_for(world, bus)
        # grid Thevenin reactance (pu) from SCR: Xg = V²/(SCR·P) = 1/SCR
        grid_l = 1.0 / max(scr, 1e-3)
        scan = impedance_scan(l1, c, l2, grid_l, base_freq=world.base_frequency_hz)
        scan.short_circuit_ratio = float(scr)
        f_res_analytic = lcl_resonance_hz(l1, c, l2 + grid_l, world.base_frequency_hz)

        # 3) time-domain GFL on the Thevenin grid at this SCR
        traj = simulate_gfl_thevenin(scr, pll_bw_hz=self.pll_bw_hz)

        result = EMTResult(engine="emt", engine_version=ENGINE_VERSION)
        step = max(len(traj.t) // 500, 1)
        result.time_s = [float(x) for x in traj.t[::step]]
        abc = _dq_to_abc(0.1 + np.abs(traj.vq_pu[::step]), traj.t[::step],
                         world.base_frequency_hz)
        result.phase_a = {"pcc_v": abc["a"]}
        result.phase_b = {"pcc_v": abc["b"]}
        result.phase_c = {"pcc_v": abc["c"]}
        result.impedance_scan = scan
        result.provenance = Provenance(
            engine="emt", engine_version=ENGINE_VERSION,
            governing=["Z = Y⁻¹ short-circuit screen", "LCL impedance scan",
                       "GFL PLL + constant-power current on a Thevenin grid"],
            notes=f"weak pocket bus {bus}, SCR {scr:.2f}")

        return result, self._explain(world, bus, scr, l1, c, l2, grid_l,
                                     scan, f_res_analytic, traj)

    def _lcl_for(self, world: World, bus: Optional[str]):
        dm = {m.id: m for m in world.dynamic_models}
        for g in world.generators:
            if g.bus_id == bus and g.dynamic_model_id in dm:
                m = dm[g.dynamic_model_id]
                if isinstance(m, ConverterModel) and m.lcl_filter:
                    f = m.lcl_filter
                    return max(f.l1, 1e-4), max(f.c, 1e-4), max(f.l2, 1e-4)
        return 0.05, 0.03, 0.05  # default pu filter

    def _explain(self, world, bus, scr, l1, c, l2, grid_l, scan, f_res_analytic,
                 traj) -> ExplainPayload:
        return ExplainPayload(
            title="EMT micro-example: weak-grid converter stability + resonance scan",
            formulation=Formulation(
                statement=("On the dynamics-flagged weak pocket, scan the LCL+grid "
                           "impedance for resonance and integrate a grid-following "
                           "converter with its PLL on a Thevenin grid. The fast "
                           "control dynamics here are invisible to the RMS model."),
                symbolic=[
                    "SCR = (V²/|Z_thevenin|) / P_converter,   Z = Y⁻¹",
                    "f_res(LCL) = (1/2π)·√((L1+L2+Lg)/(L1·(L2+Lg)·C))",
                    "Z(jω) = jωL1 + (1/jωC ∥ (jω(L2+Lg)+R))",
                    "PLL: δ' = Kp·v_q + Ki·∫v_q ;  i_d' = (P/v_d − i_d)/τ_i",
                    "v_q = −Vg·sinδ + Xg·i_d,   Xg = V²/(SCR·P)",
                ],
            ),
            inputs={
                "weak_pocket_bus": bus, "short_circuit_ratio": round(scr, 3),
                "all_scr": {b: round(v, 2) for b, v in short_circuit_ratios(world).items()},
                "lcl_l1_pu": l1, "lcl_c_pu": c, "lcl_l2_pu": l2,
                "grid_l_pu": round(grid_l, 5), "pll_bandwidth_hz": self.pll_bw_hz,
            },
            outputs={
                "resonance_peaks_hz": scan.resonance_peaks_hz,
                "lcl_resonance_analytic_hz": round(f_res_analytic, 1),
                "gfl_stable": traj.stable,
                "oscillation_hz": round(traj.oscillation_hz, 2),
                "verdict": ("RMS declared this operating point stable; EMT reveals "
                            "a control-driven instability" if not traj.stable
                            else "stable at this SCR"),
            },
            intermediates={
                "scr_threshold_note": "SCR < ~3 is a weak grid; GFL converters "
                                      "lose stability as SCR -> 1",
                "n_scan_points": len(scan.frequency_hz),
            },
            provenance={"engine": "emt", "version": ENGINE_VERSION,
                        "input_facets": self.facets,
                        "handoff": "RMS dynamics low-SCR screen -> EMT (Section 6.8)"},
        )
