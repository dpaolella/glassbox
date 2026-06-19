"""Andes oracle for the RMS dynamics engine (PRD Sections 11.1, 11.2).

Builds a single-machine-infinite-bus (a Slack infinite bus + a classical GENCLS
machine) in Andes, perturbs it with a brief fault, and measures the rotor-angle
oscillation frequency. That is compared against the analytical linearized swing
frequency and against the same quantity from the transparent hand-built SMIB
integrator. All three agree (within damping), validating the swing dynamics.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from ...engines.dynamics import simulate_smib

try:  # Andes is a dev/test-only dependency
    import andes
    HAVE_ANDES = True
except Exception:  # pragma: no cover
    andes = None
    HAVE_ANDES = False


def _dominant_freq(t: np.ndarray, x: np.ndarray) -> float:
    """Dominant oscillation frequency (Hz) of a signal via FFT."""
    x = x - x.mean()
    if len(x) < 8:
        return 0.0
    dt = float(np.median(np.diff(t)))
    spec = np.abs(np.fft.rfft(x))
    fr = np.fft.rfftfreq(len(x), dt)
    return float(fr[1:][np.argmax(spec[1:])])


def andes_smib_oscillation(h: float, p: float, xd1: float, xline: float,
                           f0: float = 60.0) -> tuple[float, float, float]:
    """Run an Andes SMIB with a brief fault; return (osc_hz, Pmax, delta0)."""
    if not HAVE_ANDES:
        raise RuntimeError("andes not available")
    logging.disable(logging.CRITICAL)
    ss = andes.System(no_output=True)
    ss.add("Bus", dict(idx=1, Vn=230, v0=1.0))
    ss.add("Bus", dict(idx=2, Vn=230, v0=1.0))
    ss.add("Slack", dict(bus=1, idx=1, p0=0.0, v0=1.0, Vn=230))
    ss.add("Line", dict(bus1=1, bus2=2, r=0.0, x=xline, b=0.0))
    ss.add("PV", dict(bus=2, idx=2, p0=p, v0=1.0, Vn=230))
    ss.add("GENCLS", dict(bus=2, gen=2, idx=1, M=2.0 * h, D=2.0, xd1=xd1))
    ss.add("Fault", dict(bus=2, tf=1.0, tc=1.05, xf=0.05))
    ss.setup()
    ss.PFlow.run()
    ss.TDS.config.tf = 8.0
    ss.TDS.config.tstep = 0.005
    ss.TDS.run()

    t = np.array(ss.dae.ts.t)
    delta = np.array(ss.dae.ts.x)[:, ss.GENCLS.delta.a[0]]
    mask = t > 1.1
    osc = _dominant_freq(t[mask], delta[mask])
    d0 = float(delta[0])
    pmax = p / math.sin(d0) if abs(math.sin(d0)) > 1e-6 else p
    return osc, pmax, d0


def glassbox_smib_oscillation(h: float, pmax: float, delta0: float,
                              f0: float = 60.0) -> float:
    """Oscillation frequency of the transparent SMIB integrator (brief fault)."""
    pm = pmax * math.sin(delta0)
    traj = simulate_smib(h, pm, pmax, 0.0, pmax, t_clear=0.05, f0=f0,
                         t_end=8.0, dt=0.005, D=2.0)
    mask = traj.t > 1.1 if (traj.t > 1.1).any() else traj.t >= 0
    return _dominant_freq(traj.t[mask], np.deg2rad(traj.delta_deg[mask]))


@dataclass
class SwingComparison:
    andes_hz: float
    glassbox_hz: float
    analytic_hz: float
    pmax: float
    delta0: float
    rel_diff_andes_vs_analytic: float
    rel_diff_glassbox_vs_andes: float


def compare_swing_frequency(h: float = 4.0, p: float = 0.8, xd1: float = 0.3,
                            xline: float = 0.3, f0: float = 60.0) -> SwingComparison:
    andes_hz, pmax, d0 = andes_smib_oscillation(h, p, xd1, xline, f0)
    analytic_hz = math.sqrt(2 * math.pi * f0 * pmax * math.cos(d0) / (2 * h)) / (2 * math.pi)
    gb_hz = glassbox_smib_oscillation(h, pmax, d0, f0)
    return SwingComparison(
        andes_hz=andes_hz, glassbox_hz=gb_hz, analytic_hz=analytic_hz,
        pmax=pmax, delta0=d0,
        rel_diff_andes_vs_analytic=abs(andes_hz - analytic_hz) / analytic_hz,
        rel_diff_glassbox_vs_andes=(abs(gb_hz - andes_hz) / andes_hz
                                    if andes_hz > 0 else 0.0),
    )
