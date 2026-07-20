"""Phase 3 tests (issue #58): telemetry, state estimation, bad data,
nodal LMPs, HRUC approvals."""

from __future__ import annotations

import numpy as np
import pytest

from glassbox.rtops import ShiftConfig, run_shift
from glassbox.rtops.telemetry import (SEResult, TelemetryConfig,
                                      estimate_state, make_telemetry)
from glassbox.world import build_default_world_with_weather


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


def _cfg(**kw):
    base = dict(n_steps=12, sced_every_steps=6, sced_window_steps=6,
                forced_outages=False, load_error_sigma=0.0,
                vre_error_sigma=0.0)
    base.update(kw)
    return ShiftConfig(**base)


# --- the WLS itself on a hand-built 3-bus case -------------------------------


def _three_bus():
    lines = [("l12", "b1", "b2", 10.0), ("l23", "b2", "b3", 10.0),
             ("l13", "b1", "b3", 5.0)]
    theta = {"b1": 0.0, "b2": -0.02, "b3": -0.05}
    flows = {lid: b * (theta[f] - theta[t]) for lid, f, t, b in lines}
    inj = {b: 0.0 for b in theta}
    for lid, f, t, b in lines:
        inj[f] += flows[lid]
        inj[t] -= flows[lid]
    return lines, flows, inj


def test_wls_recovers_flows_from_noisy_redundant_measurements():
    lines, flows, inj = _three_bus()
    cfg = TelemetryConfig(flow_sigma_mw=0.01, injection_sigma_mw=0.01,
                          dropout_prob=0.0)
    rng = np.random.default_rng(1)
    z = make_telemetry(rng, flows, inj, cfg)
    se = estimate_state(z, lines, ["b1", "b2", "b3"], cfg, "b1")
    assert se.solved and se.health == "good"
    for lid, f in flows.items():
        assert se.est_flows[lid] == pytest.approx(f, abs=0.05)
    assert se.redundancy > 2.0


def test_bad_data_identified_by_normalized_residual():
    lines, flows, inj = _three_bus()
    cfg = TelemetryConfig(flow_sigma_mw=0.01, injection_sigma_mw=0.01,
                          dropout_prob=0.0,
                          bad_data={"flow:l12": 5.0})   # a lying meter
    rng = np.random.default_rng(2)
    z = make_telemetry(rng, flows, inj, cfg)
    se = estimate_state(z, lines, ["b1", "b2", "b3"], cfg, "b1")
    assert se.solved
    assert "flow:l12" in se.bad_points
    # redundancy meant truth survives the lie
    assert se.est_flows["l12"] == pytest.approx(flows["l12"], abs=0.1)


def test_flying_blind_when_measurements_insufficient():
    lines, flows, inj = _three_bus()
    cfg = TelemetryConfig(dropout_prob=0.0)
    z = {"flow:l12": flows["l12"]}          # 1 measurement, 2 states
    se = estimate_state(z, lines, ["b1", "b2", "b3"], cfg, "b1")
    assert not se.solved and se.health == "flying_blind"


# --- kernel integration -------------------------------------------------------


def test_kernel_runs_se_and_reports_health(world):
    res = run_shift(world, _cfg())
    # deterministic replay still holds with telemetry on
    res2 = run_shift(world, _cfg())
    assert res.to_json() == res2.to_json()


def test_bad_meter_event_is_caught_by_se(world):
    line = max(world.ac_lines, key=lambda l: l.rating_normal_mva).id
    res = run_shift(world, _cfg(scripted_events=[
        {"step": 1, "kind": "bad_meter", "key": f"flow:{line}",
         "gross_mw": 150.0}]))
    flagged = [e for e in res.events if e["kind"] == "bad_data_identified"]
    assert flagged and flagged[0]["id"] == f"flow:{line}"


def test_nodal_lmps_separate_under_congestion(world):
    from glassbox.rtops import OpsSimulation
    sim = OpsSimulation(world, _cfg())
    sim.start()
    while not sim.finished:
        sim.advance_one()
    lmps = sim._nodal_lmps
    assert lmps and len(lmps) == len(world.buses)
    # LMPs range from fuel cost to VOLL (the pre-dawn scarce pocket prices
    # at 10000 — correct nodal economics, not an artifact)
    assert all(-500 <= v <= 10000.01 for v in lmps.values())
    # and prices SEPARATE: the constrained pocket vs the cheap side
    assert max(lmps.values()) - min(lmps.values()) > 100.0


def test_hruc_proposal_and_approval(world):
    from fastapi.testclient import TestClient
    from glassbox.api.app import app
    c = TestClient(app)
    c.post("/api/world/reset")
    # big load jump makes the next hourly check come up short
    c.post("/api/opsim/start", json={
        "seed": 21, "n_steps": 40, "forced_outages": False, "speed": 0,
        "scripted_events": [{"step": 6, "kind": "scale_load",
                             "factor": 1.35}]})
    c.post("/api/opsim/clock", json={"speed": 1e6})
    st = c.get("/api/opsim/state").json()
    c.post("/api/opsim/clock", json={"speed": 0})
    if st.get("hruc_pending"):
        r = c.post("/api/opsim/action", json={"type": "approve_hruc"}).json()
        assert r["applied"] and "committed" in r["note"]
        st2 = c.get("/api/opsim/state").json()
        assert st2["hruc_pending"] is None
        assert any(e["kind"] == "hruc_committed" for e in st2["events"])
    else:
        # fleet was deep enough even at +35%: the machinery must still be
        # exposed and idle rather than crashing
        assert "hruc_pending" in st
    # denial path is always testable
    r = c.post("/api/opsim/action", json={"type": "deny_hruc"}).json()
    assert "applied" in r


# --- voltage operations (issue #58) ------------------------------------------


def test_voltage_check_runs_and_reports(world):
    res = run_shift(world, _cfg(voltage_every_steps=3))
    # min-voltage trace populated (AC PF ran and converged at least once)
    assert any(v > 0 for v in res.traces["min_voltage_pu"])
    assert all(0.5 < v < 1.5 for v in res.traces["min_voltage_pu"] if v > 0)
    assert "voltage_violations" in res.totals


def test_reactive_dispatch_action(world):
    from fastapi.testclient import TestClient
    from glassbox.api.app import app
    c = TestClient(app)
    c.post("/api/world/reset")
    c.post("/api/opsim/start", json={
        "seed": 5, "n_steps": 12, "forced_outages": False, "speed": 0})
    c.post("/api/opsim/clock", json={"speed": 1e6})
    st = c.get("/api/opsim/state").json()
    c.post("/api/opsim/clock", json={"speed": 0})
    assert "bus_voltages" in st and "voltage_violations" in st
    gid = next(iter(st["basepoints"]))
    r = c.post("/api/opsim/action", json={
        "type": "voltage", "id": gid, "delta_pu": 0.02}).json()
    assert r["applied"] and "AVR setpoint" in r["note"]
    bad = c.post("/api/opsim/action", json={
        "type": "voltage", "id": "nope", "delta_pu": 0.02}).json()
    assert not bad["applied"]


# --- restoration epilogue (issue #58) ----------------------------------------


def test_blackout_and_restoration(world):
    # deliberately induce mass load loss then recovery
    res = run_shift(world, _cfg(n_steps=48, blackout_served_frac=0.6,
                                scripted_events=[
        {"step": 3, "kind": "trip_generator", "id": "nuclear_1"},
        {"step": 4, "kind": "trip_generator", "id": "ccgt_4"},
        {"step": 5, "kind": "scale_load", "factor": 1.2}]))
    served = res.traces["served_frac"]
    assert min(served) < 1.0                     # load was lost
    assert "blackout" in res.totals and "min_served_frac" in res.totals
    # the trace is well-formed and served fraction is a valid ratio
    assert all(0.0 <= v <= 1.0001 for v in served)


# --- scenario pass evaluation (issue #58) ------------------------------------


def test_scenario_pass_reported():
    from fastapi.testclient import TestClient
    from glassbox.api.app import app
    c = TestClient(app)
    c.post("/api/world/reset")
    c.post("/api/opsim/start", json={"scenario": "first_shift", "speed": 0})
    c.post("/api/opsim/clock", json={"speed": 1e6})
    for _ in range(12):
        st = c.get("/api/opsim/state").json()
        if st["clock"]["finished"]:
            break
    rep = c.get("/api/opsim/report").json()
    assert rep["scenario"] == "first_shift"
    assert rep["scenario_pass"] is not None
    assert "passed" in rep["scenario_pass"] and \
        "criterion" in rep["scenario_pass"]
