"""Build-mode v2 editing tests (issue #28): journal, patch, god mode."""

from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def client():
    from glassbox.api.app import app

    c = TestClient(app)
    c.post("/api/world/reset")
    yield c
    c.post("/api/world/reset")


def test_place_patch_undo_redo_roundtrip(client):
    # place a proposal with an honest cost preview
    r = client.post("/api/world/candidates",
                    json={"technology": "solar_pv", "bus_id": "A5"})
    assert r.status_code == 200
    d = r.json()
    cid = d["created"]
    assert d["collection"] == "expansion_candidates"
    assert d["lcoe_per_mwh"] and d["expected_capacity_factor"]

    # patch is validated: bad type rejected, good value journaled
    bad = client.patch(f"/api/world/expansion_candidates/{cid}",
                       json={"fields": {"build_max_mw": "not-a-number"}})
    assert bad.status_code == 422
    ok = client.patch(f"/api/world/expansion_candidates/{cid}",
                      json={"fields": {"build_max_mw": 250.0}})
    assert ok.status_code == 200

    # protected + unknown fields rejected
    assert client.patch(f"/api/world/expansion_candidates/{cid}",
                        json={"fields": {"id": "hax"}}).status_code == 422
    assert client.patch(f"/api/world/expansion_candidates/{cid}",
                        json={"fields": {"nope": 1}}).status_code == 422

    # undo the patch, then the placement; redo brings the placement back
    st = client.get("/api/world/journal").json()
    assert st["can_undo"]
    assert client.post("/api/world/undo").json()["undone"].startswith("edit")
    assert client.post("/api/world/undo").json()["undone"] == f"place {cid}"
    ids = [c["id"] for c in client.get("/api/entities/expansion_candidates").json()]
    assert cid not in ids
    assert client.post("/api/world/redo").json()["redone"] == f"place {cid}"
    ids = [c["id"] for c in client.get("/api/entities/expansion_candidates").json()]
    assert cid in ids
    client.post("/api/world/undo")  # leave clean


def test_god_mode_creates_real_asset_and_delete_is_undoable(client):
    r = client.post("/api/world/candidates",
                    json={"technology": "ccgt", "bus_id": "C7", "as_asset": True})
    assert r.status_code == 200
    d = r.json()
    assert d["collection"] == "generators"
    gid = d["created"]
    gens = [g["id"] for g in client.get("/api/entities/generators").json()]
    assert gid in gens

    # bulldoze it, then undo the bulldoze
    assert client.delete(f"/api/world/generators/{gid}").status_code == 200
    gens = [g["id"] for g in client.get("/api/entities/generators").json()]
    assert gid not in gens
    client.post("/api/world/undo")
    gens = [g["id"] for g in client.get("/api/entities/generators").json()]
    assert gid in gens
    client.post("/api/world/undo")  # undo the placement too


def test_buses_are_not_editable(client):
    r = client.patch("/api/world/buses/A1", json={"fields": {"x": 0}})
    assert r.status_code == 400
