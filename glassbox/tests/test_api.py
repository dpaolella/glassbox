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


def test_scenario_presets_and_run():
    presets = client.get("/api/scenario/presets").json()
    assert {p["key"] for p in presets} >= {"nodal_vs_zonal_cem", "carbon_vs_none"}
    # run a small CEM scenario through the HTTP layer
    sc = {"id": "api_cem", "layer": "cem", "spatial_operator": "aggregate",
          "temporal_map_id": "representative_days", "weather_years": [0],
          "n_rep_days": 2}
    r = client.post("/api/scenario/run", json=sc)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total_cost"] > 0
    assert body["explain"]["formulation"]["symbolic"]


def test_powerflow_scenario_run():
    sc = {"id": "api_pf", "layer": "pf", "weather_years": [0],
          "pf_dispatch_mode": "nodal"}
    r = client.post("/api/scenario/run", json=sc)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["converged"] is True
    assert body["summary"]["losses_mw"] > 0
    assert "Newton-Raphson" in body["explain"]["title"]


def test_dynamics_scenario_run():
    sc = {"id": "api_dyn", "layer": "dyn", "weather_years": [0],
          "dyn_inertia_scale": 0.3}
    r = client.post("/api/scenario/run", json=sc)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["rocof_hz_per_s"] != 0
    assert "critical_clearing_time_s" in body["explain"]["outputs"]


def test_emt_scenario_run():
    sc = {"id": "api_emt", "layer": "emt", "weather_years": [0],
          "emt_scr_override": 1.2, "emt_pll_bw_hz": 30.0}
    r = client.post("/api/scenario/run", json=sc)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["gfl_stable"] is False
    assert body["explain"]["outputs"]["resonance_peaks_hz"]


def test_all_six_layers_have_presets():
    presets = client.get("/api/scenario/presets").json()
    layers = set()
    for p in presets:
        layers.add(p["a"]["layer"])
    assert {"cem", "pcm", "ra", "pf", "dyn", "emt"} <= layers


def test_weather_ground_truth():
    sites = client.get("/api/weather/sites").json()
    wind = next(s for s in sites if s["kind"] == "wind")
    gt = client.get(f"/api/weather/ground-truth/{wind['id']}?kind=availability").json()
    assert gt["n_years"] >= 1
    assert len(gt["per_year_means"]) == gt["n_years"]
