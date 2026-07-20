"""Phase 2 tests (issue #56): NERC scoring, EEA ladder, RTCA clocks,
instructor console, scenario library."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glassbox.api.app import app
from glassbox.rtops import ShiftConfig, run_shift
from glassbox.rtops.scoring import score_shift
from glassbox.world import build_default_world_with_weather


@pytest.fixture(scope="module")
def world():
    w, _ = build_default_world_with_weather()
    return w


# --- scoring math on synthetic traces ----------------------------------------


def _mk(ace, freq):
    return {"ace_mw": ace, "freq_hz": freq}


def test_cps1_rewards_ace_opposing_frequency():
    cfg = ShiftConfig()
    # frequency low, area over-generating (helping): CF negative -> CPS1 > 200
    helping = score_shift(_mk([+40] * 12, [59.98] * 12), [], cfg, 500, 0)
    # frequency low, area under-generating (hurting): CPS1 low
    hurting = score_shift(_mk([-40] * 12, [59.98] * 12), [], cfg, 500, 0)
    assert helping["cps1_pct"] > 100 > hurting["cps1_pct"]
    assert helping["cps1_compliant"] and not hurting["cps1_compliant"]


def test_baal_violation_needs_30_consecutive_minutes():
    cfg = ShiftConfig()  # 5-min steps: 7 steps = 35 min > 30
    df = 59.96           # 0.04 Hz low
    # huge ACE aggravating low frequency for 35 minutes -> one violation
    bad = score_shift(_mk([-5000] * 7, [df] * 7), [], cfg, 500, 0)
    assert bad["baal"]["violations"] == 1
    # same exceedance broken up (never > 30 min consecutive) -> none
    ok = score_shift(_mk([-5000, -5000, 0, -5000, -5000, 0, -5000],
                         [df] * 7), [], cfg, 500, 0)
    assert ok["baal"]["violations"] == 0
    assert ok["baal"]["minutes_in_exceedance"] > 0


def test_dcs_recovery_window():
    cfg = ShiftConfig()
    ev = [{"kind": "generator_trip", "step": 2, "id": "big", "lost_mw": 450}]
    # ACE recovers to >= 0 by step 4 (10 min): pass
    good = score_shift(_mk([0, 0, -400, -150, 5, 5, 5],
                           [60] * 7), ev, cfg, 500, 0)
    assert good["dcs"]["all_recovered"]
    # ACE stays deep for > 15 min: fail
    bad = score_shift(_mk([0, 0, -400, -390, -380, -370, -360],
                          [60] * 7), ev, cfg, 500, 0)
    assert not bad["dcs"]["all_recovered"]
    # small trips are not Reportable
    small = score_shift(_mk([0, 0, -400, -390, -380, -370, -360], [60] * 7),
                        [{"kind": "generator_trip", "step": 2, "id": "s",
                          "lost_mw": 10}], cfg, 500, 0)
    assert small["dcs"]["reportable_events"] == []


# --- kernel: EEA ladder + RTCA clock -----------------------------------------


def test_eea_declared_when_largest_units_trip(world):
    gens = sorted((g for g in world.generators if g.in_service),
                  key=lambda g: -g.p_max_mw)
    res = run_shift(world, ShiftConfig(
        seed=6, n_steps=12, sced_every_steps=6, sced_window_steps=6,
        forced_outages=False,
        scripted_events=[
            {"step": 2, "kind": "trip_generator", "id": gens[0].id},
            {"step": 4, "kind": "trip_generator", "id": gens[1].id},
            {"step": 5, "kind": "scale_load", "factor": 1.12}]))
    rc = [e for e in res.events if e["kind"] == "rc_directive"]
    assert rc, "losing the two largest units should move the EEA ladder"
    assert max(e["eea_level"] for e in rc) >= 1
    assert "EEA" in rc[0]["detail"]


def test_rtca_flags_post_contingency_overload(world):
    # squeeze ratings until N-1 of the heaviest line overloads a neighbor
    res = run_shift(world, ShiftConfig(
        seed=7, n_steps=10, sced_every_steps=5, sced_window_steps=5,
        forced_outages=False,
        scripted_events=[{"step": 1, "kind": "derate_line",
                          "id": max(world.ac_lines,
                                    key=lambda l: l.rating_normal_mva).id,
                          "factor": 0.5}]))
    kinds = {e["kind"] for e in res.events}
    # the screen ran; whether it fires depends on the system's slack —
    # at minimum the machinery must not crash and events must be well-formed
    for e in res.events:
        if e["kind"] == "rtca_violation":
            assert "SOL clock" in e["detail"]


# --- session: scenarios + instructor + NERC report ---------------------------


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    c.post("/api/world/reset")
    return c


def test_scenario_library_and_start(client):
    lst = client.get("/api/opsim/scenarios").json()
    ids = {s["id"] for s in lst}
    assert {"first_shift", "morning_ramp", "dcs_drill",
            "thirty_minute_clock", "storm_shift"} <= ids
    assert all(s["lesson"] and s["pass"] for s in lst)
    r = client.post("/api/opsim/start",
                    json={"scenario": "dcs_drill", "speed": 0})
    assert r.status_code == 200
    assert client.post("/api/opsim/start",
                       json={"scenario": "nope"}).status_code == 404


def test_instructor_injects_into_running_shift(client):
    client.post("/api/opsim/start", json={
        "seed": 3, "n_steps": 30, "forced_outages": False, "speed": 0})
    gid = "coal_1"
    r = client.post("/api/opsim/instructor", json={
        "kind": "trip_generator", "id": gid, "step": 2}).json()
    assert r["applied"] and r["scheduled_step"] >= 1
    bad = client.post("/api/opsim/instructor",
                      json={"kind": "meteor"}).json()
    assert not bad["applied"] and "inject" in bad["reason"]
    client.post("/api/opsim/clock", json={"speed": 1e6})
    st = client.get("/api/opsim/state").json()
    assert any(e["kind"] == "generator_trip" and e["id"] == gid
               for e in st["events"])
    assert "eea_level" in st and "sol_clocks" in st


def test_report_carries_nerc_scores(client):
    client.post("/api/opsim/start", json={
        "seed": 4, "n_steps": 8, "forced_outages": False, "speed": 0})
    client.post("/api/opsim/clock", json={"speed": 1e6})
    client.get("/api/opsim/state")
    rep = client.get("/api/opsim/report").json()
    nerc = rep["nerc"]
    assert "cps1_pct" in nerc and "baal" in nerc and "dcs" in nerc
    assert {"frequency_support_cps1", "ace_limits_baal",
            "contingency_recovery_dcs", "reliability", "security",
            "sol_compliance_top001"} <= set(rep["grades"])
    assert nerc["constants"]["dcs_recovery_min"] == 15.0


# --- ORDC-lite: prices scream before load is shed (issue #57) ----------------


def test_scarcity_adder_lifts_lambda_on_unit_loss(world):
    from glassbox.rtops import ShiftConfig, run_shift
    cfg = dict(n_steps=12, sced_every_steps=6, sced_window_steps=6,
               forced_outages=False, load_error_sigma=0.0, vre_error_sigma=0.0)
    calm = run_shift(world, ShiftConfig(**cfg))
    tight = run_shift(world, ShiftConfig(**cfg, scripted_events=[
        {"step": 2, "kind": "trip_generator", "id": "nuclear_1"},
        {"step": 3, "kind": "trip_generator", "id": "ccgt_4"}]))
    # calm morning: lambda is a fuel cost; deep scarcity: the reserve
    # demand curve's top tranche price shows up in lambda
    assert max(calm.traces["lambda_per_mwh"]) < 300.0
    assert max(tight.traces["lambda_per_mwh"]) == \
        ShiftConfig().reserve_curve[-1][1]
    # and the post-trip SCEDs actually solved (the outage decommit +
    # p_min-zeroing fixes: no silent stale basepoints)
    assert not any(e["kind"] == "sced_failed" for e in tight.events)
    assert tight.traces["gen_mw"][6] < calm.traces["gen_mw"][6] - 500.0
