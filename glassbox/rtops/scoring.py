"""NERC-style shift scoring: CPS1, BAAL, DCS (issue #56 Phase 2, PRD §8.1).

Real definitions at toy scale, constants documented:

* **CPS1** (BAL-001): CF = mean[ (ACE_i / (-10B)) * dF_i ] / eps1^2, score =
  (2 - CF) x 100. Rewards ACE that OPPOSES frequency error. Compliant >= 100%.
  eps1 (the interconnection's RMS 1-min frequency-error bound) is 0.02 Hz here.
* **BAAL** (BAL-001-2): a dynamic ACE limit that tightens as frequency strays:
  BAAL(Fa) = -10B x (FTL - Fs)^2 / (Fa - Fs), FTL = Fs +/- 0.05 Hz here.
  Exceeding it for more than 30 consecutive minutes is a violation.
* **DCS** (BAL-002): after a Reportable contingency (unit trip >= MSSC
  threshold), recover ACE to >= min(0, pre-disturbance) within 15 minutes.

The 5-minute step stands in for the clock-minute; every simplification is a
named constant, not a silent choice.
"""

from __future__ import annotations

import numpy as np

EPS1_HZ = 0.02
FTL_BAND_HZ = 0.05
BAAL_LIMIT_MIN = 30.0
DCS_RECOVERY_MIN = 15.0
MSSC_FRACTION = 0.8          # trips >= 80% of the largest unit are Reportable


def score_shift(traces: dict, events: list[dict], cfg, largest_unit_mw: float,
                unserved_mwh: float) -> dict:
    ace = np.asarray(traces["ace_mw"], dtype=float)
    freq = np.asarray(traces["freq_hz"], dtype=float)
    n = len(ace)
    if n == 0:
        return {"note": "no steps completed"}
    b10 = max(10.0 * abs(cfg.bias_mw_per_0p1hz), 1e-6)  # MW/Hz (never zero)
    fs = 60.0
    step_min = cfg.step_minutes

    # --- CPS1 ---------------------------------------------------------------
    df = freq - fs
    # B is negative by convention, so -10B = +10|B| = b10: over-generation
    # (ACE > 0) when frequency is LOW (dF < 0) makes the term negative,
    # i.e. it helps, and CPS1 = (2 - CF) rises above 100.
    cf = float(np.mean((ace / b10) * df)) / (EPS1_HZ ** 2)
    cps1 = (2.0 - cf) * 100.0

    # --- BAAL ---------------------------------------------------------------
    exceed = np.zeros(n, dtype=bool)
    for i in range(n):
        if abs(df[i]) < 1e-6:
            continue                                  # on-frequency: no limit
        baal = b10 * (FTL_BAND_HZ ** 2) / abs(df[i])
        # limit applies on the side that HURTS frequency (ACE and dF same sign
        # means over/under-generation aggravating the error)
        if ace[i] * df[i] > 0 and abs(ace[i]) > baal:
            exceed[i] = True
    violations, run = 0, 0
    max_run_min = 0.0
    for e in exceed:
        run = run + 1 if e else 0
        max_run_min = max(max_run_min, run * step_min)
        if run * step_min > BAAL_LIMIT_MIN:
            violations += 1
            run = 0
    baal_result = {"minutes_in_exceedance": round(float(exceed.sum()) * step_min, 1),
                   "longest_run_min": round(max_run_min, 1),
                   "violations": violations}

    # --- DCS ----------------------------------------------------------------
    dcs_events = []
    for ev in events:
        if ev.get("kind") != "generator_trip":
            continue
        lost = float(ev.get("lost_mw", 0.0))
        if lost < MSSC_FRACTION * largest_unit_mw:
            continue
        k = ev["step"]
        pre = ace[k - 1] if k >= 1 else 0.0
        target = min(0.0, pre)
        horizon = k + int(DCS_RECOVERY_MIN / step_min)
        recovered = any(ace[j] >= target - 1e-6
                        for j in range(k + 1, min(horizon + 1, n)))
        dcs_events.append({"step": k, "unit": ev.get("id"),
                           "lost_mw": lost, "recovered_in_15min": recovered})
    dcs_pass = all(d["recovered_in_15min"] for d in dcs_events) \
        if dcs_events else True

    # --- grades -------------------------------------------------------------
    def grade_cps1(v):
        return "A" if v >= 100 else ("B" if v >= 90 else "C")

    return {
        "cps1_pct": round(cps1, 1),
        "cps1_compliant": cps1 >= 100.0,
        "baal": baal_result,
        "dcs": {"reportable_events": dcs_events, "all_recovered": dcs_pass},
        "unserved_mwh": round(unserved_mwh, 3),
        "grades": {
            "frequency_support_cps1": grade_cps1(cps1),
            "ace_limits_baal": "A" if baal_result["violations"] == 0 else "C",
            "contingency_recovery_dcs": "A" if dcs_pass else "C",
            "reliability": "A" if unserved_mwh < 0.5
                           else ("B" if unserved_mwh < 5 else "C"),
        },
        "constants": {"eps1_hz": EPS1_HZ, "ftl_band_hz": FTL_BAND_HZ,
                      "baal_limit_min": BAAL_LIMIT_MIN,
                      "dcs_recovery_min": DCS_RECOVERY_MIN,
                      "mssc_fraction": MSSC_FRACTION},
    }
