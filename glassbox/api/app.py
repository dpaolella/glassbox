"""FastAPI app: world, schema introspection, entities, time series, operators.

PRD Section 3.4 / 9. Phase 0 surface: enough to drive the network canvas, the
layer-filtered inspector (Section 9.2), the SI/per-unit toggle (Section 4.3), the
time-series plots (Section 9.6), and the operator explain() panels (Section 9.3).
Engine endpoints (explain payloads for solved runs) arrive with the engines in
later phases.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from ..operators import (
    AttributeProjection,
    SpatialMode,
    SpatialProjection,
    TemporalProjection,
    build_full_chronology_map,
    build_representative_days_map,
)
from ..schema import ENTITY_MODELS, FACET_LABELS, Facet, field_metadata
from .service import COLLECTION_MODELS, service

app = FastAPI(title="Glassbox API", version="0.1.0",
              description="An inspectable multi-paradigm grid modeling sandbox")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- meta / health -----------------------------------------------------------


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/world/summary")
def world_summary():
    w = service.world
    return {
        "id": w.id, "name": w.name, "description": w.description,
        "base_power_mva": w.base_power_mva, "base_frequency_hz": w.base_frequency_hz,
        "reference_bus_id": w.reference_bus_id,
        "counts": {name: len(getattr(w, name)) for name in COLLECTION_MODELS},
        "n_dynamic_models": len(w.dynamic_models),
        "n_time_series": len(w.time_series_store.series),
        "n_weather_years": (w.weather_model.n_years if w.weather_model else 0),
    }


# --- schema introspection (drives the layer selector) -----------------------


@app.get("/api/schema/facets")
def schema_facets():
    return [{"code": f.value, "label": FACET_LABELS[f]} for f in Facet]


@app.get("/api/schema/entities")
def schema_entities():
    out = {}
    for type_name, model in ENTITY_MODELS.items():
        out[type_name] = field_metadata(model)
    return out


@app.get("/api/schema/entity/{type_name}/fields")
def schema_entity_fields(type_name: str, facet: Optional[str] = None):
    if type_name not in ENTITY_MODELS:
        raise HTTPException(404, f"unknown entity type {type_name}")
    model = ENTITY_MODELS[type_name]
    if facet:
        try:
            fields = AttributeProjection(facet).fields_for(model)
        except ValueError:
            raise HTTPException(400, f"unknown facet {facet}")
    else:
        fields = list(model.model_fields)
    return {"type": type_name, "facet": facet, "fields": fields}


# --- entities / inspector ---------------------------------------------------


@app.get("/api/entities/{collection}")
def list_entities(collection: str, facet: Optional[str] = None):
    if collection not in COLLECTION_MODELS:
        raise HTTPException(404, f"unknown collection {collection}")
    items = service.collection(collection)
    if facet:
        op = AttributeProjection(facet)
        return [op.project_entity(it) for it in items]
    return [it.model_dump(mode="json") for it in items]


@app.get("/api/entity/{collection}/{entity_id}")
def inspect_entity(collection: str, entity_id: str, facet: Optional[str] = None):
    if collection not in COLLECTION_MODELS:
        raise HTTPException(404, f"unknown collection {collection}")
    try:
        return service.inspect_entity(collection, entity_id, facet)
    except KeyError:
        raise HTTPException(404, f"{entity_id} not found in {collection}")


@app.get("/api/graph")
def network_graph():
    return service.graph()


# --- time series (Section 9.6) ----------------------------------------------


@app.get("/api/timeseries")
def list_timeseries():
    store = service.world.time_series_store
    return [ts.model_dump(mode="json") for ts in store.series.values()]


@app.get("/api/timeseries/{ts_id}")
def get_timeseries(ts_id: str,
                   start: int = Query(0, ge=0),
                   length: int = Query(8760, ge=1, le=87600),
                   downsample: int = Query(1, ge=1, le=24)):
    store = service.world.time_series_store
    if ts_id not in store:
        raise HTTPException(404, f"time series {ts_id} not found")
    arr = store.get(ts_id)
    end = min(start + length, arr.shape[0])
    window = arr[start:end]
    if downsample > 1:
        n = (window.shape[0] // downsample) * downsample
        window = window[:n].reshape(-1, downsample).mean(axis=1)
    meta = store.meta(ts_id)
    return {
        "id": ts_id, "unit": meta.unit, "kind": meta.kind.value,
        "start": start, "length": int(window.shape[0]), "downsample": downsample,
        "values": window.tolist(),
    }


# --- weather ground truth (the multi-weather-year lesson, Section 7) ---------


@app.get("/api/weather/sites")
def weather_sites():
    return [s.model_dump(mode="json") for s in service.world.weather_sites]


@app.get("/api/weather/ground-truth/{site_id}")
def weather_ground_truth(site_id: str, kind: str = "availability"):
    """The true distribution vs per-year means — 'truth' vs 'what one year implies'."""
    store = service.world.time_series_store
    ts_id = f"{kind}__{site_id}"
    if ts_id not in store:
        raise HTTPException(404, f"no {kind} series for site {site_id}")
    arr = store.get(ts_id)
    meta = store.meta(ts_id)
    hpy = meta.hours_per_year
    n_years = max(arr.shape[0] // hpy, 1)
    yearly = arr[: n_years * hpy].reshape(n_years, hpy)
    hist, edges = np.histogram(arr, bins=40, density=True)
    return {
        "site_id": site_id, "kind": kind, "unit": meta.unit,
        "n_years": n_years,
        "truth": {"mean": float(arr.mean()), "std": float(arr.std()),
                  "bin_edges": edges.tolist(), "density": hist.tolist()},
        "per_year_means": [float(y.mean()) for y in yearly],
        "per_year_p5": [float(np.percentile(y, 5)) for y in yearly],
    }


# --- operator explain panels (Section 9.3) ----------------------------------


@app.get("/api/operators/attribute/{facet}/explain")
def attribute_explain(facet: str):
    try:
        op = AttributeProjection(facet)
    except ValueError:
        raise HTTPException(400, f"unknown facet {facet}")
    op.apply(service.world)
    return op.explain().model_dump(mode="json")


@app.get("/api/operators/spatial/{mode}/explain")
def spatial_explain(mode: str):
    try:
        op = SpatialProjection(SpatialMode(mode))
    except ValueError:
        raise HTTPException(400, f"unknown spatial mode {mode}")
    op.apply(service.world)
    return op.explain().model_dump(mode="json")


@app.get("/api/operators/spatial/{mode}/view")
def spatial_view(mode: str):
    try:
        op = SpatialProjection(SpatialMode(mode))
    except ValueError:
        raise HTTPException(400, f"unknown spatial mode {mode}")
    view = op.apply(service.world)
    return {
        "mode": view.mode.value,
        "node_ids": view.node_ids,
        "bus_to_node": view.bus_to_node,
        "node_members": view.node_members,
        "transfer_limits_mw": {f"{a}->{b}": v
                               for (a, b), v in view.transfer_limits_mw.items()},
        "collapsed_branch_ids": view.collapsed_branch_ids,
        "crossing_branch_ids": view.crossing_branch_ids,
    }


@app.get("/api/operators/temporal/explain")
def temporal_explain(kind: str = "full_chronology",
                     n_days: int = Query(12, ge=2, le=60)):
    """Explain a temporal map. For representative_days, builds it from the
    default world's load+VRE signals so the chronology-loss lesson is concrete."""
    if kind == "full_chronology":
        tmap = build_full_chronology_map(service.world.weather_model.hours_per_year)
    elif kind == "representative_days":
        store = service.world.time_series_store
        signals = []
        for ts_id, ts in store.series.items():
            if ts.kind.value in ("availability", "demand"):
                signals.append(store.get(ts_id)[: ts.hours_per_year])
        if not signals:
            raise HTTPException(400, "no signals to cluster")
        import numpy as np

        series = np.vstack(signals)
        tmap = build_representative_days_map(series, n_days=n_days)
    else:
        raise HTTPException(400, f"unknown temporal kind {kind}")
    op = TemporalProjection(tmap)
    op.apply()
    payload = op.explain().model_dump(mode="json")
    payload["map"] = {"id": tmap.id, "kind": tmap.kind.value,
                      "n_periods": len(tmap.representative_timesteps)}
    return payload
