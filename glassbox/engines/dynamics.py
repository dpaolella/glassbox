"""Dynamic stability (RMS / phasor) engine (PRD Section 6.6) — facets: dyn, core.

Two transparent RMS models, integrated by hand (Section 2.3 / 6.1):

  * System Frequency Response (SFR): an aggregated swing equation with governor
    primary response and optional fast frequency response. Demonstrates that the
    frequency nadir and RoCoF worsen as synchronous inertia is replaced by
    inverters, that PFR/FFR arrest the decline, and that grid-forming converters
    provide effective inertia grid-following ones do not.

  * Single-Machine-Infinite-Bus (SMIB) transient stability: the swing equation
    with a fault applied and cleared, demonstrating that a longer fault-clearing
    time pushes a machine past transient stability (the equal-area criterion).
    Validated against the analytical critical clearing time.

Cross-layer handoffs (Section 6.7): the dynamics result is turned into a minimum
inertia / RoCoF SystemConstraint, a fast-frequency-response ReserveProduct
requirement, and a stability-limited Interface limit that flow upward into PCM
and CEM. Initial machine loading comes from a solved power flow / dispatch
snapshot (power flow -> dynamics handoff).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..explain import ExplainPayload, Formulation
from ..schema import DynamicsResult, Provenance, World
from ..schema.dynamic_models import ConverterModel, SynchronousMachineModel
from .base import ENGINE_VERSION

HOURS_PER_YEAR = 8760


# --- aggregate inertia from the online mix ----------------------------------


@dataclass
class FrequencySystem:
    h_total_mws: float          # total kinetic energy constant, MW·s
    sync_mva_online: float      # online synchronous MVA
    gfm_mva_online: float       # online grid-forming MVA
    total_gen_mw: float
    total_load_mw: float
    governor_gain_mw_per_hz: float
    headroom_mw: float          # primary-reserve headroom
    largest_unit_mw: float
    f0: float
    online_units: list[str] = field(default_factory=list)


def assemble_frequency_system(world: World, dispatch: dict[str, float],
                              inertia_scale: float = 1.0,
                              gfm_provides_inertia: bool = True) -> FrequencySystem:
    """Aggregate inertia, governor gain and headroom from online units.

    A unit is "online" if its dispatch is positive. Synchronous machines provide
    H·S kinetic energy; grid-forming converters provide virtual_inertia·S;
    grid-following converters provide none (the inertia lesson).
    """
    f0 = world.base_frequency_hz
    dm = {m.id: m for m in world.dynamic_models}
    h_total = 0.0
    sync_mva = 0.0
    gfm_mva = 0.0
    gov_gain = 0.0
    headroom = 0.0
    online: list[str] = []
    largest = 0.0
    total_gen = 0.0

    def unit_records():
        for g in world.generators:
            if not g.in_service or g.status.value == "retired":
                continue
            yield (g.id, g.bus_id, g.dynamic_model_id, g.mva_base, g.p_max_mw, g.is_vre)
        for h in world.hydro_units:
            if not h.in_service:
                continue
            yield (h.id, h.bus_id, h.dynamic_model_id, h.mva_base, h.p_max_mw, False)

    for uid, _bus, mid, mva, pmax, is_vre in unit_records():
        p = dispatch.get(uid, 0.0)
        if p <= 1e-6:
            continue
        online.append(uid)
        total_gen += p
        largest = max(largest, p)
        model = dm.get(mid) if mid else None
        if isinstance(model, SynchronousMachineModel):
            h_total += model.h_s * mva
            sync_mva += mva
            # governor primary response gain Kg = P_rated / (droop · f0)
            droop = model.governor.droop_r if model.governor else 0.05
            gov_gain += pmax / (max(droop, 1e-3) * f0)
            headroom += max(pmax - p, 0.0)
        elif isinstance(model, ConverterModel):
            if model.control_mode == "grid_forming":
                gfm_mva += mva
                if gfm_provides_inertia and model.virtual_inertia_s > 0:
                    h_total += model.virtual_inertia_s * mva
                # grid-forming droop also contributes fast power-frequency response
                gov_gain += pmax / (max(model.droop_p_f, 1e-3) * f0)
                headroom += max(pmax - p, 0.0)
            # grid-following: no inertia, no firm primary response

    h_total *= inertia_scale
    total_load = sum(p for p in dispatch.values() if p > 0)  # served ~ load

    return FrequencySystem(
        h_total_mws=h_total, sync_mva_online=sync_mva, gfm_mva_online=gfm_mva,
        total_gen_mw=total_gen, total_load_mw=total_load,
        governor_gain_mw_per_hz=gov_gain, headroom_mw=headroom,
        largest_unit_mw=largest, f0=f0, online_units=online)


# --- system frequency response simulation -----------------------------------


@dataclass
class FrequencyTrajectory:
    t: np.ndarray
    f_hz: np.ndarray            # absolute frequency (f0 + Δf)
    nadir_hz: float
    rocof_hz_per_s: float
    settling_hz: float
    quasi_steady_hz: float


def simulate_frequency_response(sys: FrequencySystem, event_mw: float,
                                enable_ffr: bool = False, ffr_mw: float = 0.0,
                                ffr_tc_s: float = 0.2, gov_tc_s: float = 4.0,
                                damping_pu: float = 1.0,
                                t_end: float = 20.0, dt: float = 0.01) -> FrequencyTrajectory:
    """Integrate the aggregated swing + governor + FFR ODEs (RK4).

    (2H/f0)·dΔf/dt = Pm + Pffr − ΔP − D·Δf
    Tg·dPm/dt = −Pm − Kg·Δf            (capped at primary-reserve headroom)
    Tffr·dPffr/dt = −Pffr + Pffr_target(Δf)   (capped at ffr_mw)
    """
    f0 = sys.f0
    H = max(sys.h_total_mws, 1e-6)
    D = damping_pu * max(sys.total_load_mw, 1.0) / f0    # MW per Hz
    Kg = sys.governor_gain_mw_per_hz
    headroom = max(sys.headroom_mw, 0.0)

    n = int(t_end / dt) + 1
    t = np.linspace(0, t_end, n)
    df = np.zeros(n)      # frequency deviation (Hz)
    Pm = np.zeros(n)      # governor response (MW)
    Pf = np.zeros(n)      # FFR response (MW)

    def deriv(df_, Pm_, Pf_):
        ddf = (Pm_ + Pf_ - event_mw - D * df_) * f0 / (2 * H)
        Pm_target = float(np.clip(-Kg * df_, 0.0, headroom))
        dPm = (Pm_target - Pm_) / gov_tc_s
        if enable_ffr:
            Pf_target = float(np.clip(-(ffr_mw / 0.5) * df_, 0.0, ffr_mw))
        else:
            Pf_target = 0.0
        dPf = (Pf_target - Pf_) / ffr_tc_s
        return ddf, dPm, dPf

    for k in range(1, n):
        df0_, Pm0_, Pf0_ = df[k - 1], Pm[k - 1], Pf[k - 1]
        k1 = deriv(df0_, Pm0_, Pf0_)
        k2 = deriv(df0_ + 0.5 * dt * k1[0], Pm0_ + 0.5 * dt * k1[1], Pf0_ + 0.5 * dt * k1[2])
        k3 = deriv(df0_ + 0.5 * dt * k2[0], Pm0_ + 0.5 * dt * k2[1], Pf0_ + 0.5 * dt * k2[2])
        k4 = deriv(df0_ + dt * k3[0], Pm0_ + dt * k3[1], Pf0_ + dt * k3[2])
        df[k] = df0_ + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        Pm[k] = Pm0_ + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        Pf[k] = Pf0_ + dt / 6 * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2])

    f_abs = f0 + df
    rocof = -event_mw * f0 / (2 * H)
    return FrequencyTrajectory(
        t=t, f_hz=f_abs, nadir_hz=float(f_abs.min()),
        rocof_hz_per_s=float(rocof), settling_hz=float(f_abs[-1]),
        quasi_steady_hz=float(f_abs[-1]))


# --- SMIB transient stability -----------------------------------------------


@dataclass
class SMIBTrajectory:
    t: np.ndarray
    delta_deg: np.ndarray
    omega_pu: np.ndarray
    stable: bool
    max_angle_deg: float


def simulate_smib(H: float, pm: float, pmax_pre: float, pmax_fault: float,
                  pmax_post: float, t_clear: float, f0: float = 60.0,
                  t_end: float = 5.0, dt: float = 0.001, D: float = 2.0) -> SMIBTrajectory:
    """Classical swing equation for one machine vs an infinite bus.

    dδ/dt = 2π f0 · Δω ;  2H · dΔω/dt = Pm − Pmax·sinδ − D·Δω
    Pmax switches: pre-fault -> during-fault (t<t_clear) -> post-fault.
    Loss of synchronism is detected when δ runs past π and keeps growing.
    """
    delta0 = np.arcsin(np.clip(pm / pmax_pre, -1, 1))
    n = int(t_end / dt) + 1
    t = np.linspace(0, t_end, n)
    delta = np.zeros(n)
    omega = np.zeros(n)
    delta[0] = delta0

    def pmax_at(tt):
        return pmax_fault if tt < t_clear else pmax_post

    def deriv(d_, w_, tt):
        dd = 2 * np.pi * f0 * w_
        dw = (pm - pmax_at(tt) * np.sin(d_) - D * w_) / (2 * H)
        return dd, dw

    stable = True
    for k in range(1, n):
        tt = t[k - 1]
        d_, w_ = delta[k - 1], omega[k - 1]
        k1 = deriv(d_, w_, tt)
        k2 = deriv(d_ + 0.5 * dt * k1[0], w_ + 0.5 * dt * k1[1], tt + 0.5 * dt)
        k3 = deriv(d_ + 0.5 * dt * k2[0], w_ + 0.5 * dt * k2[1], tt + 0.5 * dt)
        k4 = deriv(d_ + dt * k3[0], w_ + dt * k3[1], tt + dt)
        delta[k] = d_ + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        omega[k] = w_ + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        if delta[k] > 3 * np.pi:   # ran away -> out of step
            stable = False
            delta = delta[: k + 1]
            omega = omega[: k + 1]
            t = t[: k + 1]
            break
    return SMIBTrajectory(t=t, delta_deg=np.rad2deg(delta), omega_pu=omega,
                          stable=stable, max_angle_deg=float(np.rad2deg(delta.max())))


def critical_clearing_time_analytical(H: float, pm: float, pmax: float,
                                      f0: float = 60.0) -> float:
    """Equal-area critical clearing time (during-fault Pe=0, pre=post=pmax)."""
    d0 = np.arcsin(np.clip(pm / pmax, -1, 1))
    dmax = np.pi - d0
    cos_dc = np.cos(dmax) + (pm / pmax) * (dmax - d0)
    cos_dc = float(np.clip(cos_dc, -1, 1))
    dc = np.arccos(cos_dc)
    # during fault Pe=0: δ(t) = δ0 + (π f0 Pm / (2H)) t²  -> solve δ(tcr)=δc
    accel = np.pi * f0 * pm / (2 * H)
    return float(np.sqrt(max(dc - d0, 0.0) / accel))


def critical_clearing_time_numerical(H: float, pm: float, pmax: float,
                                     f0: float = 60.0) -> float:
    """Bisection on clearing time using the SMIB integrator (fault Pe=0)."""
    lo, hi = 0.0, 1.0
    for _ in range(22):
        mid = 0.5 * (lo + hi)
        traj = simulate_smib(H, pm, pmax, 0.0, pmax, mid, f0=f0, D=0.0)
        if traj.stable:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --- engine ------------------------------------------------------------------


class DynamicsEngine:
    facets = ["dyn", "core"]
    name = "dyn"

    def __init__(self, hour: Optional[int] = None, weather_year: int = 0,
                 event_mw: Optional[float] = None, enable_ffr: bool = False,
                 ffr_mw: float = 0.0, inertia_scale: float = 1.0,
                 fault_clear_s: float = 0.15):
        self.hour = hour
        self.weather_year = weather_year
        self.event_mw = event_mw
        self.enable_ffr = enable_ffr
        self.ffr_mw = ffr_mw
        self.inertia_scale = inertia_scale
        self.fault_clear_s = fault_clear_s

    def run(self, world: World) -> tuple[DynamicsResult, ExplainPayload]:
        from .powerflow import peak_load_hour, snapshot_dispatch

        hour = self.hour if self.hour is not None else peak_load_hour(world, self.weather_year)
        dispatch = snapshot_dispatch(world, hour, self.weather_year, mode="nodal")
        sys = assemble_frequency_system(world, dispatch, inertia_scale=self.inertia_scale)

        event = self.event_mw if self.event_mw is not None else sys.largest_unit_mw
        traj = simulate_frequency_response(sys, event, enable_ffr=self.enable_ffr,
                                           ffr_mw=self.ffr_mw)

        # SMIB transient stability for the largest synchronous machine
        smib, cct_a, cct_n = self._smib(world, dispatch, sys)

        result = DynamicsResult(engine="dyn", engine_version=ENGINE_VERSION)
        # subsample the frequency trajectory for transport
        step = max(len(traj.t) // 400, 1)
        result.time_s = [float(x) for x in traj.t[::step]]
        result.states = {"frequency_hz": [float(x) for x in traj.f_hz[::step]]}
        if smib is not None:
            sstep = max(len(smib.t) // 400, 1)
            result.states["smib_delta_deg"] = [float(x) for x in smib.delta_deg[::sstep]]
            result.rotor_angle_separation_deg = smib.max_angle_deg
        result.frequency_nadir_hz = traj.nadir_hz
        result.rocof_hz_per_s = traj.rocof_hz_per_s

        result.provenance = Provenance(
            engine="dyn", engine_version=ENGINE_VERSION,
            governing=["aggregated swing 2H/f0·df/dt = ΔP_mech − ΔP_elec − D·Δf",
                       "governor primary response", "SMIB equal-area criterion"],
            notes=f"operating point: dispatch snapshot at hour {hour}")
        return result, self._explain(world, sys, event, traj, smib, cct_a, cct_n)

    def _smib(self, world, dispatch, sys):
        # pick the largest online synchronous machine
        dm = {m.id: m for m in world.dynamic_models}
        best = None
        for g in world.generators:
            if not g.in_service or g.status.value == "retired":
                continue
            if dispatch.get(g.id, 0) > 0 and g.dynamic_model_id in dm:
                m = dm[g.dynamic_model_id]
                if isinstance(m, SynchronousMachineModel):
                    if best is None or dispatch[g.id] > best[1]:
                        best = (g, dispatch[g.id], m)
        if best is None:
            return None, 0.0, 0.0
        g, p_mw, m = best
        H = m.h_s
        pmax = max(p_mw / g.mva_base / np.sin(np.deg2rad(45)), 1.2)  # transfer limit pu
        pm = min(p_mw / g.mva_base, 0.95 * pmax)
        cct_a = critical_clearing_time_analytical(H, pm, pmax, world.base_frequency_hz)
        cct_n = critical_clearing_time_numerical(H, pm, pmax, world.base_frequency_hz)
        smib = simulate_smib(H, pm, pmax, 0.0, pmax, self.fault_clear_s,
                             f0=world.base_frequency_hz)
        return smib, cct_a, cct_n

    def _explain(self, world, sys, event, traj, smib, cct_a, cct_n) -> ExplainPayload:
        f0 = sys.f0
        return ExplainPayload(
            title="Dynamic stability (RMS): frequency response + transient stability",
            formulation=Formulation(
                statement=("After losing the largest online unit, integrate the "
                           "aggregated swing equation with governor and optional "
                           "fast frequency response; separately test a machine's "
                           "transient stability against a fault via SMIB."),
                symbolic=[
                    "H_sys = Σ_sync H_i·S_i + Σ_gfm H_virt·S_i   (GFL contributes 0)",
                    "(2H/f0)·dΔf/dt = Pm + Pffr − ΔP − D·Δf",
                    "RoCoF(0⁺) = −ΔP·f0 / (2·H_sys)",
                    "dδ/dt = 2πf0·Δω ;  2H·dΔω/dt = Pm − Pmax·sinδ − D·Δω",
                    "equal-area: cosδ_cr = cosδ_max + (Pm/Pmax)(δ_max − δ0)",
                ],
            ),
            inputs={
                "H_sys_mws": round(sys.h_total_mws, 1),
                "sync_mva_online": round(sys.sync_mva_online, 1),
                "gfm_mva_online": round(sys.gfm_mva_online, 1),
                "inertia_scale": self.inertia_scale,
                "event_loss_mw": round(event, 1),
                "governor_gain_mw_per_hz": round(sys.governor_gain_mw_per_hz, 1),
                "primary_headroom_mw": round(sys.headroom_mw, 1),
                "ffr_enabled": self.enable_ffr, "ffr_mw": self.ffr_mw,
                "fault_clear_s": self.fault_clear_s,
            },
            outputs={
                "frequency_nadir_hz": round(traj.nadir_hz, 4),
                "nadir_deviation_hz": round(traj.nadir_hz - f0, 4),
                "rocof_hz_per_s": round(traj.rocof_hz_per_s, 4),
                "quasi_steady_hz": round(traj.quasi_steady_hz, 4),
                "smib_stable": (None if smib is None else smib.stable),
                "smib_max_angle_deg": (None if smib is None else round(smib.max_angle_deg, 1)),
                "critical_clearing_time_s": round(cct_n, 4),
            },
            intermediates={
                "critical_clearing_time_analytical_s": round(cct_a, 4),
                "online_units": sys.online_units,
                "rocof_limit_note": "RoCoF beyond ~1 Hz/s typically trips protection",
            },
            provenance={"engine": "dyn", "version": ENGINE_VERSION,
                        "input_facets": self.facets,
                        "handoff": "power flow -> dynamics (Section 6.7)"},
        )


# --- dynamics -> operations/planning handoffs (Section 6.7) ------------------


def derive_stability_requirements(world: World, dispatch: dict[str, float],
                                  rocof_limit_hz_per_s: float = 1.0,
                                  reference_event_mw: Optional[float] = None) -> dict:
    """Turn a dynamics result into upward constraints (Section 6.7).

    Returns a minimum system inertia (to keep RoCoF within limit for the
    reference event), a fast-frequency-response requirement, and whether a
    stability-limited interface limit should be flagged. These flow up into PCM
    and CEM as SystemConstraint / ReserveProduct / Interface.limit_source.
    """
    f0 = world.base_frequency_hz
    sys = assemble_frequency_system(world, dispatch)
    event = reference_event_mw if reference_event_mw is not None else sys.largest_unit_mw
    # RoCoF = event·f0/(2H)  <=  limit  =>  H_min = event·f0/(2·limit)
    h_min_mws = event * f0 / (2 * rocof_limit_hz_per_s)
    inertia_deficit = max(h_min_mws - sys.h_total_mws, 0.0)
    # FFR sized to arrest the nadir if inertia is short (simple proxy)
    ffr_mw = round(0.5 * event) if inertia_deficit > 0 else 0.0
    return {
        "h_sys_mws": round(sys.h_total_mws, 1),
        "h_min_mws": round(h_min_mws, 1),
        "inertia_deficit_mws": round(inertia_deficit, 1),
        "rocof_limit_hz_per_s": rocof_limit_hz_per_s,
        "reference_event_mw": round(event, 1),
        "ffr_requirement_mw": ffr_mw,
        "min_synchronous_units_needed": inertia_deficit > 0,
    }
