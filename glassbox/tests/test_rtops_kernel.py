"""Phase 0b exit tests (issue #56): the kernel's doctrine, proven.

Short scenarios keep runtime sane; each run is a real staged simulation
(DA-UC -> RT-SCED with fixed commitment -> AGC -> protection)."""

from __future__ import annotations

import numpy as np
import pytest

from glassbox.rtops import OpsSimulation, ShiftConfig, run_shift
from glassbox.world import build_default_world_with_weather


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


def _cfg(**kw) -> ShiftConfig:
    base = dict(n_steps=12, sced_every_steps=6, sced_window_steps=6,
                forced_outages=False)
    base.update(kw)
    return ShiftConfig(**base)


# --- determinism (the replay guarantee) --------------------------------------


def test_same_seed_same_shift(world):
    a = run_shift(world, _cfg(seed=7)).to_json()
    b = run_shift(world, _cfg(seed=7)).to_json()
    assert a == b


def test_different_seed_different_actuals(world):
    a = run_shift(world, _cfg(seed=1, load_error_sigma=0.03))
    b = run_shift(world, _cfg(seed=2, load_error_sigma=0.03))
    assert a.traces["load_mw"] != b.traces["load_mw"]


# --- conservation every step --------------------------------------------------


def test_unserved_is_exactly_the_shortfall(world):
    res = run_shift(world, _cfg(seed=3, load_error_sigma=0.02))
    for gen, load, uns in zip(res.traces["gen_mw"], res.traces["load_mw"],
                              res.traces["unserved_mw"]):
        assert uns == pytest.approx(max(0.0, load - gen), abs=1e-2)


def test_switch_state_restored_after_run(world):
    sim = OpsSimulation(world, _cfg(scripted_events=[
        {"step": 2, "kind": "trip_generator", "id": world.generators[0].id}]))
    before = {s.id: s.open for s in sim.world.switches}
    sim.run()
    after = {s.id: s.open for s in sim.world.switches}
    assert before == after


# --- the feedforward: DA commitment binds RT ---------------------------------


def test_da_commitment_feeds_forward(world):
    res = run_shift(world, _cfg())
    committed = res.da_summary["committed_units"]
    assert committed, "the default world should have UC units"
    for gid, arr in committed.items():
        assert set(np.unique(arr)) <= {0.0, 1.0}
    assert res.da_summary["da_cost"] > 0
    assert res.events[0]["kind"] == "turnover_briefing"


# --- forecast error is the antagonist -----------------------------------------


def test_no_error_no_ace_excursion(world):
    calm = run_shift(world, _cfg(seed=5, load_error_sigma=0.0,
                                 vre_error_sigma=0.0))
    noisy = run_shift(world, _cfg(seed=5, load_error_sigma=0.04))
    assert calm.totals["max_freq_dev_hz"] <= noisy.totals["max_freq_dev_hz"]
    # with no forecast error AGC has almost nothing to chase
    assert calm.totals["max_freq_dev_hz"] < 0.01


# --- unit trip: protection through breakers + SFR transient -------------------


def test_scripted_generator_trip(world):
    gid = max((g for g in world.generators if g.in_service),
              key=lambda g: g.p_max_mw).id
    res = run_shift(world, _cfg(
        n_steps=8, scripted_events=[
            {"step": 3, "kind": "trip_generator", "id": gid}]))
    ev = next(e for e in res.events if e["kind"] == "generator_trip")
    assert ev["id"] == gid and ev["step"] == 3
    # the SFR overlay: a real nadir below nominal, finite RoCoF
    assert "sfr" in ev, ev.get("sfr_error")
    assert ev["sfr"]["nadir_hz"] < 60.0
    assert ev["sfr"]["rocof_hz_per_s"] < 0.0


# --- Grid2Op protection doctrine ----------------------------------------------


def test_soft_overflow_trips_after_allowed_steps_then_recloses(world):
    # probe: find the most loaded line, then derate it into sustained overflow
    probe = OpsSimulation(world, _cfg(n_steps=1))
    probe.run()
    worst = max(probe.last_flows, key=lambda l: abs(probe.last_flows[l]))
    line = next(l for l in world.ac_lines if l.id == worst)
    flow = abs(probe.last_flows[worst])
    factor = 0.8 * flow / line.rating_emergency_mva  # ~1.25x emergency, not hard
    res = run_shift(world, _cfg(
        n_steps=12, reconnect_steps=3, scripted_events=[
            {"step": 1, "kind": "derate_line", "id": worst, "factor": factor}]))
    trip = next(e for e in res.events if e["kind"] == "line_trip"
                and e["id"] == worst)
    warns = [e for e in res.events if e["kind"] == "overload_warning"
             and e["id"] == worst and e["step"] < trip["step"]]
    # warned exactly NB_TIMESTEP_OVERFLOW_ALLOWED times, tripped on the next
    # (the derate persists, so later episodes may warn again after reclose)
    assert len(warns) == 2
    assert trip["step"] == warns[-1]["step"] + 1
    assert trip["reason"].startswith("soft overflow")
    reclose = next(e for e in res.events if e["kind"] == "line_reclosed"
                   and e["id"] == worst)
    assert reclose["step"] == trip["step"] + 3  # out for exactly N steps


# --- API ------------------------------------------------------------------------


def test_opsim_api_runs_a_short_shift():
    from fastapi.testclient import TestClient

    from glassbox.api.app import app
    client = TestClient(app)
    client.post("/api/world/reset")
    r = client.post("/api/opsim/run", json={
        "seed": 11, "n_steps": 6, "forced_outages": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["traces"]["freq_hz"]) == 6
    assert body["totals"]["energy_cost"] > 0
    assert body["events"][0]["kind"] == "turnover_briefing"
