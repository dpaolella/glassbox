"""API smoke tests (PRD Sections 3.4, 9)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from glassbox.api import app

client = TestClient(app)


def test_world_summary():
    r = client.get("/api/world/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["counts"]["buses"] == 26
    assert data["n_weather_years"] >= 1


def test_facets_endpoint():
    r = client.get("/api/schema/facets")
    assert r.status_code == 200
    codes = [f["code"] for f in r.json()]
    assert "ops" in codes and "dyn" in codes


def test_inspect_generator_layer_filtered():
    # pick a generator id
    gens = client.get("/api/entities/generators").json()
    gid = gens[0]["id"]
    ops = client.get(f"/api/entity/generators/{gid}?facet=ops").json()
    names = {f["name"] for f in ops["fields"]}
    assert "heat_rate_mmbtu_per_mwh" in names
    assert "capex_per_mw" not in names  # inv-only hidden at ops layer

    inv = client.get(f"/api/entity/generators/{gid}?facet=inv").json()
    inv_names = {f["name"] for f in inv["fields"]}
    assert "capex_per_mw" in inv_names


def test_per_unit_toggle_present_for_power_fields():
    gens = client.get("/api/entities/generators").json()
    gid = gens[0]["id"]
    ops = client.get(f"/api/entity/generators/{gid}?facet=ops").json()
    pmax = next(f for f in ops["fields"] if f["name"] == "p_max_mw")
    assert pmax["per_unit"] is not None
    assert pmax["per_unit"]["unit"] == "pu"


def test_graph_endpoint():
    g = client.get("/api/graph").json()
    assert len(g["nodes"]) == 26
    assert g["edges"]
    assert len(g["zones"]) == 3


def test_timeseries_fetch_and_downsample():
    ts = client.get("/api/timeseries").json()
    assert ts
    sid = next(t["id"] for t in ts if t["kind"] == "availability")
    r = client.get(f"/api/timeseries/{sid}?start=0&length=168&downsample=1").json()
    assert r["length"] == 168
    r2 = client.get(f"/api/timeseries/{sid}?start=0&length=168&downsample=24").json()
    assert r2["length"] == 7


def test_operator_explain_surfaces_information_loss():
    agg = client.get("/api/operators/spatial/aggregate/explain").json()
    assert agg["information_loss"]
    attr = client.get("/api/operators/attribute/ops/explain").json()
    assert "fields_in_scope" in attr["outputs"]


def test_weather_ground_truth():
    sites = client.get("/api/weather/sites").json()
    wind = next(s for s in sites if s["kind"] == "wind")
    gt = client.get(f"/api/weather/ground-truth/{wind['id']}?kind=availability").json()
    assert gt["n_years"] >= 1
    assert len(gt["per_year_means"]) == gt["n_years"]
