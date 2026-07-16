"""Import/export via grid-rosetta (issue #53).

Skips cleanly when the optional translate extra is not installed — the
feature is opt-in and everything else must work without it.
"""

from __future__ import annotations

import pytest

rosetta = pytest.importorskip("rosetta")
pypsa = pytest.importorskip("pypsa")


def _foreign_network(hours=24):
    import numpy as np
    import pandas as pd

    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(hours))
    n.add("Bus", "b1", v_nom=220.0)
    n.add("Bus", "b2", v_nom=220.0)
    n.add("Line", "l12", bus0="b1", bus1="b2", x=10.0, r=1.0, s_nom=300.0)
    n.add("Generator", "coal1", bus="b1", p_nom=250.0, carrier="coal",
          marginal_cost=25.0, control="Slack")
    n.add("Generator", "weird1", bus="b2", p_nom=50.0,
          carrier="unobtainium-chp")
    t = np.arange(hours)
    n.add("Load", "city", bus="b2",
          p_set=pd.Series(150.0 + 50.0 * np.sin(t / 24 * 2 * np.pi),
                          index=n.snapshots))
    return n


def test_availability_reports_schemas_and_hubs():
    from glassbox.interop import availability

    info = availability()
    assert info["available"]
    assert "pypsa" in info["schemas"] and "glassbox" in info["schemas"]
    assert "sienna" in info["hubs"]


def test_import_produces_world_with_receipts():
    from glassbox.interop import import_model

    result = import_model(_foreign_network(), "pypsa", hours=24)
    world = result.world
    assert len(world.buses) == 2
    assert {g.id for g in world.generators} == {"coal1", "weird1"}

    # the receipts: free-text carrier hit the closed enum, counted
    manual = [m for hop in result.manifest["hops"]
              for m in hop["manual_mapping_required"]]
    assert any(m["entity"] == "weird1" for m in manual)
    # the world round-trips through glassbox's own persistence contract
    assert world.reference_bus_id in {b.id for b in world.buses}


def test_import_export_roundtrip_carries_sidecar(tmp_path):
    """world -> pypsa export uses the sidecar kept from import, and the
    export writes model + sidecar.json + coverage.json."""
    from glassbox.interop import export_model, import_model

    result = import_model(_foreign_network(), "pypsa", hub="sienna",
                          hours=24)
    out = export_model(result.world, "pypsa", tmp_path / "exp", hours=24,
                       sidecar=result.sidecar)
    assert (tmp_path / "exp" / "network.nc").exists()
    assert (tmp_path / "exp" / "coverage.json").exists()
    assert (tmp_path / "exp" / "sidecar.json").exists()
    assert out.manifest["route"]  # the receipts rode along


def test_api_import_endpoint_swaps_world_and_returns_manifest(tmp_path):
    from fastapi.testclient import TestClient

    from glassbox.api import app
    from glassbox.api.service import service

    client = TestClient(app)
    r = client.get("/api/translate/availability")
    assert r.status_code == 200 and r.json()["available"]

    src = tmp_path / "net.nc"
    _foreign_network().export_to_netcdf(str(src))
    try:
        r = client.post("/api/translate/import",
                        json={"source": str(src), "schema_name": "pypsa",
                              "hours": 24})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["world"]["counts"]["buses"] == 2
        assert data["manifest"]["totals"]["manual_mapping_required"] >= 1
        # the live world is now the imported one
        assert {g.id for g in service.world.generators} == \
            {"coal1", "weird1"}

        # export it back out through the API
        r = client.post("/api/translate/export",
                        json={"schema_name": "pypsa", "name": "rt test",
                              "hours": 24})
        assert r.status_code == 200, r.text
        assert r.json()["exported"].endswith("rt_test")
    finally:
        service.reset()

    # inbound-only spokes refuse politely (no glassbox -> plexos bridge)
    r = client.post("/api/translate/export",
                    json={"schema_name": "plexos", "name": "nope"})
    assert r.status_code == 400
    assert "no direct bridge glassbox -> plexos" in r.json()["detail"]
