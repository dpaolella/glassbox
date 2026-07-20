"""Interchange + OperatingArea tests (issue #56 Phase 2 remainder).

The full Reporting ACE frame: scheduled interchange enters SCED, the external
area's bias carries imbalance across the tie until it saturates, and losing
the tie islands the frequency response."""

from __future__ import annotations

import pytest

from glassbox.rtops import ShiftConfig, run_shift
from glassbox.schema import OperatingArea
from glassbox.world import build_default_world_with_weather


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


def _cfg(**kw) -> ShiftConfig:
    base = dict(n_steps=12, sced_every_steps=6, sced_window_steps=6,
                forced_outages=False, load_error_sigma=0.0,
                vre_error_sigma=0.0)
    base.update(kw)
    return ShiftConfig(**base)


def test_schedule_enters_sced_and_ramps(world):
    # export 120 MW from the second hour on
    res = run_shift(world, _cfg(n_steps=24,
                                interchange_schedule_mw=[0.0, 120.0, 120.0]))
    ni = res.traces["ni_sched_mw"]
    assert ni[0] == 0.0 and ni[-1] == 120.0
    # the :50 -> :10 ramp: intermediate values exist at the hour boundary
    assert any(0.0 < v < 120.0 for v in ni), "schedule must ramp, not step"
    # SCED covered the export: generation runs ~120 MW above the same
    # moment in a no-schedule baseline (deterministic: sigma = 0)
    base = run_shift(world, _cfg(n_steps=24))
    k = 18  # deep in hour 2, ramp done
    assert res.traces["gen_mw"][k] - base.traces["gen_mw"][k] == \
        pytest.approx(120.0, abs=20.0)
    # and balancing stayed clean while doing it
    assert abs(res.traces["ace_mw"][k]) < 10.0


def test_tie_stiffens_frequency(world):
    # sluggish AGC so the shock is still unabsorbed at trace time (end of
    # step) — the default gain corrects a 5% shock within one 5-min step
    shock = [{"step": 4, "kind": "scale_load", "factor": 1.05}]
    slow = dict(agc_gain=0.02, agc_subticks=2)
    tied = run_shift(world, _cfg(seed=11, scripted_events=list(shock), **slow))
    islanded = run_shift(world, _cfg(seed=11, tie_capacity_mw=0.0,
                                     scripted_events=list(shock), **slow))
    # same disturbance, same ACE story, but the interconnection absorbs it:
    # frequency barely moves with the tie, and NIa visibly leans on it
    assert tied.totals["max_freq_dev_hz"] < islanded.totals["max_freq_dev_hz"]
    lean = [abs(a - s) for a, s in zip(tied.traces["ni_actual_mw"],
                                       tied.traces["ni_sched_mw"])]
    assert max(lean) > 1.0, "inadvertent interchange should carry the shock"


def test_tie_trip_islands_the_area(world):
    shock = [{"step": 2, "kind": "trip_tie"},
             {"step": 4, "kind": "scale_load", "factor": 1.05}]
    res = run_shift(world, _cfg(seed=12, scripted_events=shock))
    assert any(e["kind"] == "tie_trip" for e in res.events)
    # post-trip: no tie support at all
    assert all(a == s for a, s in zip(res.traces["ni_actual_mw"][3:],
                                      res.traces["ni_sched_mw"][3:]))
    ref = run_shift(world, _cfg(seed=12, scripted_events=[
        {"step": 4, "kind": "scale_load", "factor": 1.05}]))
    assert res.totals["max_freq_dev_hz"] > ref.totals["max_freq_dev_hz"]


def test_operating_area_entity_overrides_config(world):
    w = world.model_copy()
    w.operating_areas = [OperatingArea(
        id="area1", name="Toy BA", tie_capacity_mw=0.0,
        frequency_bias_mw_per_0p1hz=-80.0)]
    shock = [{"step": 4, "kind": "scale_load", "factor": 1.05}]
    from_entity = run_shift(w, _cfg(seed=13, scripted_events=list(shock)))
    tied = run_shift(world, _cfg(seed=13, scripted_events=list(shock)))
    # the entity's zero-capacity tie islands the area even though cfg
    # defaults would have provided one
    assert from_entity.totals["max_freq_dev_hz"] > \
        tied.totals["max_freq_dev_hz"]
