"""Telemetry + state estimation (issue #58, PRD Phase 3 'trust').

The ops-schema lesson in executable form: planning data is exact by
construction; operations only ever ESTIMATES the truth from noisy, redundant,
occasionally-lying measurements. This module:

  * synthesizes SCADA telemetry from the kernel's true DC state — line-flow
    and bus-injection measurements with per-point Gaussian noise, dropout
    (failed RTUs), and optional gross errors (a lying meter);
  * runs a real weighted-least-squares DC state estimation: z = H theta + e,
    solved in the weighted sense, exactly the linearized form of what an EMS
    runs every few minutes;
  * detects bad data by the largest-normalized-residual test and re-estimates
    with the offender removed — the classic loop;
  * reports SE HEALTH: redundancy, residual norm, and identified bad points.
    An unsolvable or degraded SE is itself an operator emergency ("flying
    blind") — the 2003 blackout's dead state estimator is the case study.

DC (angles-only) rather than full AC WLS: linear, deterministic, and it
teaches every concept — redundancy, residuals, bad-data identification —
without Newton iterations. The AC upgrade is a formulation swap, not a
redesign.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class TelemetryConfig:
    flow_sigma_mw: float = 4.0        # per line-flow measurement
    injection_sigma_mw: float = 6.0   # per bus net-injection measurement
    dropout_prob: float = 0.02        # a point simply not reporting this scan
    bad_data: dict = field(default_factory=dict)   # meas key -> gross error MW


@dataclass
class SEResult:
    solved: bool
    est_flows: dict[str, float]
    residual_norm: float
    redundancy: float                 # measurements per state — >1 needed
    bad_points: list[str]             # identified by largest normalized residual
    n_measurements: int
    n_dropped: int
    health: str                       # "good" | "degraded" | "flying_blind"

    def summary(self) -> dict:
        import math
        return {"solved": self.solved, "health": self.health,
                "residual_norm": round(self.residual_norm, 3)
                if math.isfinite(self.residual_norm) else None,
                "redundancy": round(self.redundancy, 2),
                "bad_points": self.bad_points,
                "n_measurements": self.n_measurements,
                "n_dropped": self.n_dropped}


def make_telemetry(rng, true_flows: dict[str, float],
                   true_inj: dict[str, float],
                   cfg: TelemetryConfig) -> dict[str, float]:
    """Noisy, incomplete, occasionally-lying measurements from the truth."""
    z: dict[str, float] = {}
    for lid, f in sorted(true_flows.items()):
        if rng.random() < cfg.dropout_prob:
            continue
        z[f"flow:{lid}"] = f + rng.normal(0, cfg.flow_sigma_mw)
    for bid, pinj in sorted(true_inj.items()):
        if rng.random() < cfg.dropout_prob:
            continue
        z[f"inj:{bid}"] = pinj + rng.normal(0, cfg.injection_sigma_mw)
    for key, gross in cfg.bad_data.items():
        if key in z:
            z[key] += gross           # the lying meter
    return z


def estimate_state(z: dict[str, float], lines: list[tuple[str, str, str, float]],
                   bus_ids: list[str], cfg: TelemetryConfig,
                   ref_bus: str) -> SEResult:
    """DC-WLS: solve min ||W^(1/2)(z - H theta)||^2, then the bad-data loop.

    lines: (line_id, from_bus, to_bus, susceptance b = 1/x).
    """
    n_all = len(bus_ids)
    idx = {b: i for i, b in enumerate(bus_ids)}
    ref = idx.get(ref_bus, 0)
    keep = [i for i in range(n_all) if i != ref]
    n_state = len(keep)
    col = {i: j for j, i in enumerate(keep)}

    def rows_for(keys):
        H = np.zeros((len(keys), n_state))
        zz = np.zeros(len(keys))
        wg = np.zeros(len(keys))
        for r, key in enumerate(keys):
            zz[r] = z[key]
            kind, name = key.split(":", 1)
            if kind == "flow":
                lid, fb, tb, b = next(l for l in lines if l[0] == name)
                if idx[fb] != ref:
                    H[r, col[idx[fb]]] += b
                if idx[tb] != ref:
                    H[r, col[idx[tb]]] -= b
                wg[r] = 1.0 / cfg.flow_sigma_mw ** 2
            else:                     # injection at a bus: sum of line flows
                bi = idx[name]
                for lid, fb, tb, b in lines:
                    if idx[fb] == bi:
                        if idx[fb] != ref:
                            H[r, col[idx[fb]]] += b
                        if idx[tb] != ref:
                            H[r, col[idx[tb]]] -= b
                    elif idx[tb] == bi:
                        if idx[tb] != ref:
                            H[r, col[idx[tb]]] += b
                        if idx[fb] != ref:
                            H[r, col[idx[fb]]] -= b
                wg[r] = 1.0 / cfg.injection_sigma_mw ** 2
        return H, zz, wg

    # a measurement can outlive its asset within a step (protection tripped
    # the line after the scan): estimate only against the model we still have
    known_lines = {l[0] for l in lines}
    known_buses = set(bus_ids)
    def _known(key):
        kind, name = key.split(":", 1)
        return name in known_lines if kind == "flow" else name in known_buses
    keys = sorted(k for k in z.keys() if _known(k))
    n_dropped_total = 0
    bad: list[str] = []
    for _sweep in range(3):           # at most a few bad points per scan
        if len(keys) < n_state:
            return SEResult(False, {}, float("inf"),
                            len(keys) / max(n_state, 1), bad, len(z),
                            n_dropped_total, "flying_blind")
        H, zz, wg = rows_for(keys)
        sw = np.sqrt(wg)
        theta_r, *_ = np.linalg.lstsq(H * sw[:, None], zz * sw, rcond=None)
        resid = zz - H @ theta_r
        norm_res = np.abs(resid) * sw
        worst = int(np.argmax(norm_res))
        if norm_res[worst] > 4.0:     # largest-normalized-residual test
            bad.append(keys[worst])
            keys = [k for i, k in enumerate(keys) if i != worst]
            continue
        theta = np.zeros(n_all)
        for i in keep:
            theta[i] = theta_r[col[i]]
        est_flows = {lid: b * (theta[idx[fb]] - theta[idx[tb]])
                     for lid, fb, tb, b in lines}
        redundancy = len(keys) / max(n_state, 1)
        health = "good" if redundancy >= 1.5 and not bad else \
            ("degraded" if redundancy >= 1.05 else "flying_blind")
        return SEResult(True, est_flows,
                        float(np.sqrt(np.mean(resid ** 2))), redundancy, bad,
                        len(z), len(z) - len(keys), health)
    return SEResult(True, {}, float("inf"), len(keys) / max(n_state, 1),
                    bad, len(z), len(z) - len(keys), "degraded")
