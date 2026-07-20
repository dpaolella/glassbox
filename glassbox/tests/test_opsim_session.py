"""Phase 1 session tests: the interactive control room API (issue #56)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glassbox.api.app import app


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    c.post("/api/world/reset")
    return c


def _start(client, **kw):
    body = {"seed": 9, "n_steps": 8, "forced_outages": False,
            "speed": 0.0}  # frozen: tests drive the clock explicitly
    body.update(kw)
    r = client.post("/api/opsim/start", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_start_and_frozen_clock(client):
    st = _start(client)
    assert st["clock"]["step"] == 0 and st["clock"]["speed"] == 0.0
    assert st["da_summary"]["committed_units"]
    st2 = client.get("/api/opsim/state").json()
    assert st2["clock"]["step"] == 0  # frozen clock does not advance


def test_clock_advances_with_speed(client):
    _start(client)
    # 1e6 sim-minutes per wall-minute: any wall time covers all 8 steps,
    # but MAX_STEPS_PER_POLL caps a single poll
    client.post("/api/opsim/clock", json={"speed": 1e6})
    st = client.get("/api/opsim/state").json()
    assert 0 < st["clock"]["step"] <= 24
    st = client.get("/api/opsim/state").json()
    assert st["clock"]["finished"] is True
    assert len(st["traces"]["freq_hz"]) == 8
    # the dashboard payload is complete
    assert st["lines"] and "rho_emergency" in st["lines"][0]
    assert st["basepoints"]


def test_actions_legality_explanations(client):
    _start(client, n_steps=40)   # one capped poll leaves the shift running
    client.post("/api/opsim/clock", json={"speed": 1e6})
    client.get("/api/opsim/state")
    client.post("/api/opsim/clock", json={"speed": 0})   # freeze to act

    # unknown unit: rejected with a reason, not an error code
    r = client.post("/api/opsim/action", json={
        "type": "redispatch", "id": "nope", "delta_mw": 10}).json()
    assert not r["applied"] and "no generator" in r["reason"]

    # over-capacity redispatch: the reason teaches the constraint
    st = client.get("/api/opsim/state").json()
    gid = max(st["basepoints"], key=st["basepoints"].get)
    r = client.post("/api/opsim/action", json={
        "type": "redispatch", "id": gid, "delta_mw": 1e6}).json()
    assert not r["applied"] and "physical limit" in r["reason"]

    # disconnector under load: interlock reason surfaces through the session
    subs = client.get("/api/substations").json()
    ds = next(s["id"] for sub in subs for s in sub["switches"]
              if s["kind"] == "disconnector" and s["paired_breaker_ids"])
    r = client.post("/api/opsim/action", json={
        "type": "switch", "id": ds, "open": True}).json()
    assert not r["applied"] and "interlock" in r["reason"]


def test_redispatch_and_shed_enter_the_balance(client):
    _start(client, n_steps=40)
    client.post("/api/opsim/clock", json={"speed": 1e6})
    st = client.get("/api/opsim/state").json()
    gid = max(st["basepoints"], key=st["basepoints"].get)
    r = client.post("/api/opsim/action", json={
        "type": "redispatch", "id": gid, "delta_mw": -5}).json()
    assert r["applied"]
    r = client.post("/api/opsim/action", json={
        "type": "shed_load", "mw": 25}).json()
    assert r["applied"] and "scored" in r["note"]
    st = client.get("/api/opsim/state").json()
    assert st["manual_shed_mw"] == 25.0
    assert st["redispatch"][gid] == -5.0
    kinds = {e["kind"] for e in st["events"]}
    assert {"operator_redispatch", "operator_load_shed"} <= kinds


def test_study_mode_never_mutates(client):
    _start(client, n_steps=40)
    client.post("/api/opsim/clock", json={"speed": 1e6})
    client.get("/api/opsim/state")
    before = client.get("/api/opsim/state").json()
    # what-if: open some line's bay breaker (one that exists in this layout)
    subs = client.get("/api/substations").json()
    line_ids = {l["id"] for l in before["lines"]}
    cb = next(s["id"] for sub in subs for s in sub["switches"]
              if s["kind"] == "breaker" and s["bay_equipment_id"] in line_ids)
    r = client.post("/api/opsim/study", json={
        "type": "switch", "id": cb, "open": True}).json()
    assert r["ok"] and "worst_lines" in r
    after = client.get("/api/opsim/state").json()
    assert after["lines"] == before["lines"]          # nothing changed
    assert not any(e["kind"] == "operator_switch" for e in after["events"])


def test_alarms_and_report(client):
    _start(client, n_steps=6, scripted_events=[
        {"step": 2, "kind": "trip_generator", "id": "coal_1"}])
    client.post("/api/opsim/clock", json={"speed": 1e6})
    st = client.get("/api/opsim/state").json()
    crit = [a for a in st["alarms"] if a["severity"] == "critical"]
    assert crit and st["unacked_critical"] >= 1
    r = client.post("/api/opsim/action", json={
        "type": "ack_alarm", "id": crit[0]["id"]}).json()
    assert r["applied"]
    assert client.get("/api/opsim/state").json()["unacked_critical"] \
        == st["unacked_critical"] - 1

    rep = client.get("/api/opsim/report").json()
    assert rep["finished"] and set(rep["grades"]) == {
        "reliability", "frequency_control", "security"}


def test_no_session_is_a_clean_409():
    import sys
    app_module = sys.modules["glassbox.api.app"]
    saved = app_module._ops_session["session"]
    app_module._ops_session["session"] = None
    try:
        c = TestClient(app)
        r = c.get("/api/opsim/state")
        assert r.status_code == 409 and "opsim/start" in r.json()["detail"]
    finally:
        app_module._ops_session["session"] = saved
