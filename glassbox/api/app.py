"""FastAPI app: world, schema introspection, entities, time series, operators.

PRD Section 3.4 / 9. Phase 0 surface: enough to drive the network canvas, the
layer-filtered inspector (Section 9.2), the SI/per-unit toggle (Section 4.3), the
time-series plots (Section 9.6), and the operator explain() panels (Section 9.3).
Engine endpoints (explain payloads for solved runs) arrive with the engines in
later phases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..operators import (
    AttributeProjection,
    SpatialMode,
    SpatialProjection,
    TemporalProjection,
    build_full_chronology_map,
    build_representative_days_map,
)
from ..scenario import Layer, Scenario, SpatialOperator, diff_runs, run_scenario
from ..scenario.scenario import Override
from ..schema import (
    ENTITY_MODELS,
    FACET_DESCRIPTIONS,
    FACET_ENGINE,
    FACET_LABELS,
    Facet,
    field_metadata,
)
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
    return [{"code": f.value, "label": FACET_LABELS[f],
             "description": FACET_DESCRIPTIONS[f], "engine": FACET_ENGINE[f]}
            for f in Facet]


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


@app.get("/api/series/load-scopes")
def load_scopes():
    """Regions you can aggregate load over: the whole system or each zone."""
    w = service.world
    scopes = [{"id": "all", "name": "All regions"}]
    scopes += [{"id": z.id, "name": z.name} for z in w.zones]
    return scopes


@app.get("/api/series/load")
def aggregated_load(scope: str = "all",
                    start: int = Query(0, ge=0),
                    length: int = Query(168, ge=1, le=87600),
                    downsample: int = Query(1, ge=1, le=24)):
    """Aggregated load (MW) summed across a region (zone id) or the whole system.

    Lets the user see one region's demand or the system total over any window
    (day, week, …) rather than a single load's raw series."""
    w = service.world
    store = w.time_series_store
    total = None
    n_loads = 0
    for ld in w.loads:
        if scope != "all" and ld.zone_id != scope:
            continue
        if not ld.demand_profile_id or ld.demand_profile_id not in store:
            continue
        arr = store.get(ld.demand_profile_id)
        total = arr.copy() if total is None else total + arr
        n_loads += 1
    if total is None:
        raise HTTPException(404, f"no load series for scope '{scope}'")
    end = min(start + length, total.shape[0])
    window = total[start:end]
    if downsample > 1:
        n = (window.shape[0] // downsample) * downsample
        window = window[:n].reshape(-1, downsample).mean(axis=1)
    return {
        "scope": scope, "unit": "MW", "n_loads": n_loads,
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


# --- scenarios: run, diff, presets (Sections 10, 9.5) -----------------------


def _run_payload(run) -> dict:
    return {
        "scenario": run.scenario.model_dump(mode="json"),
        "summary": run.summary,
        "result": run.result.model_dump(mode="json"),
        "explain": run.explain.model_dump(mode="json"),
        "operator_explanations": run.operator_explanations,
    }


@app.post("/api/scenario/run")
def scenario_run(scenario: Scenario):
    try:
        run = run_scenario(service.world, scenario)
    except Exception as exc:  # surface solver/build errors to the UI
        raise HTTPException(500, f"scenario run failed: {exc}")
    return _run_payload(run)


class DiffRequest(BaseModel):
    a: Scenario
    b: Scenario


class PlanOperateRequest(BaseModel):
    cem: Scenario   # the planning run whose builds get committed
    pcm: Scenario   # the operating run, executed before AND after the commit


@app.post("/api/scenario/plan_then_operate")
def scenario_plan_then_operate(req: PlanOperateRequest):
    """The planning loop (issue #9): plan -> commit builds -> operate.

    Runs the CEM scenario, materializes its builds into a committed world
    (world_with_builds), then runs the PCM scenario on both the original and
    the committed world so the payoff of the plan is directly visible.
    """
    from ..scenario import world_with_builds

    try:
        cem_run = run_scenario(service.world, req.cem)
        committed = world_with_builds(service.world, cem_run.result)
        before = run_scenario(service.world, req.pcm)
        after = run_scenario(committed, req.pcm)
    except Exception as exc:
        raise HTTPException(500, f"plan-then-operate failed: {exc}")
    return {
        "cem": _run_payload(cem_run),
        "before": _run_payload(before),
        "after": _run_payload(after),
        "diff": diff_runs(before, after),
        "committed_assets": {
            "generators": [g.id for g in committed.generators
                           if g.id.startswith("built_")],
            "storage": [st.id for st in committed.storage_units
                        if st.id.startswith("built_")],
            "lines": [ln.id for ln in committed.ac_lines
                      if ln.id.startswith("built_")],
        },
    }


@app.post("/api/scenario/diff")
def scenario_diff(req: DiffRequest):
    try:
        run_a = run_scenario(service.world, req.a)
        run_b = run_scenario(service.world, req.b)
    except Exception as exc:
        raise HTTPException(500, f"scenario diff failed: {exc}")
    return {
        "a": _run_payload(run_a),
        "b": _run_payload(run_b),
        "diff": diff_runs(run_a, run_b),
    }


@app.get("/api/scenario/presets")
def scenario_presets():
    """Canonical demonstration pairs (Section 10): each differs in one knob."""
    def cem(sid, **kw):
        base = dict(id=sid, layer=Layer.CEM, temporal_map_id="representative_days",
                    weather_years=[0], n_rep_days=4)
        base.update(kw)
        return Scenario(**base).model_dump(mode="json")

    def pcm(sid, **kw):
        base = dict(id=sid, layer=Layer.PCM, temporal_map_id="full_chronology",
                    weather_years=[0], horizon_hours=72, horizon_start=4300)
        base.update(kw)
        return Scenario(**base).model_dump(mode="json")

    def ra(sid, **kw):
        base = dict(id=sid, layer=Layer.RA, ra_n_draws=40, ra_seed=1)
        base.update(kw)
        return Scenario(**base).model_dump(mode="json")

    def pf(sid, **kw):
        base = dict(id=sid, layer=Layer.PF, weather_years=[0])
        base.update(kw)
        return Scenario(**base).model_dump(mode="json")

    def dyn(sid, **kw):
        base = dict(id=sid, layer=Layer.DYN, weather_years=[0])
        base.update(kw)
        return Scenario(**base).model_dump(mode="json")

    def emt(sid, **kw):
        base = dict(id=sid, layer=Layer.EMT, weather_years=[0])
        base.update(kw)
        return Scenario(**base).model_dump(mode="json")

    return [
        {
            "key": "few_vs_many_rep_days",
            "name": "Coarse vs fine time (capacity)",
            "lesson": "Compressing a year into too few representative days hides "
                      "chronology — storage value, ramps, and scarcity hours "
                      "change when time is resolved more finely.",
            "a": cem("cem_coarse_time", n_rep_days=2),
            "b": cem("cem_fine_time", n_rep_days=12),
        },
        {
            "key": "nodal_vs_zonal_cem",
            "name": "Nodal vs Zonal (capacity)",
            "lesson": "Nodal modeling reveals congestion and curtailment that "
                      "zonal aggregation hides.",
            "a": cem("cem_zonal", spatial_operator=SpatialOperator.AGGREGATE),
            "b": cem("cem_nodal", spatial_operator=SpatialOperator.IDENTITY),
        },
        {
            "key": "nodal_vs_zonal_pcm",
            "name": "Nodal vs Zonal (prices)",
            "lesson": "Locational marginal prices and a binding interface appear "
                      "under the nodal view and vanish under the zonal view.",
            "a": pcm("pcm_zonal", spatial_operator=SpatialOperator.AGGREGATE),
            "b": pcm("pcm_nodal", spatial_operator=SpatialOperator.IDENTITY),
        },
        {
            "key": "one_year_vs_many",
            "name": "One year vs Many",
            "lesson": "A single weather year misrepresents a VRE system; the "
                      "capacity mix shifts when many years are used.",
            "a": cem("cem_one", spatial_operator=SpatialOperator.AGGREGATE,
                     weather_years=[0]),
            "b": cem("cem_many", spatial_operator=SpatialOperator.AGGREGATE,
                     weather_years=[0, 1, 2, 3]),
        },
        {
            "key": "carbon_vs_none",
            "name": "Carbon price vs none",
            "lesson": "A carbon price reshapes the capacity mix and reduces "
                      "emissions relative to the unpriced baseline.",
            "a": cem("cem_base", spatial_operator=SpatialOperator.AGGREGATE),
            "b": cem("cem_carbon", spatial_operator=SpatialOperator.AGGREGATE,
                     overrides=[Override(kind="set_policy", policy_kind="carbon_price",
                                         value=150.0).model_dump()]),
        },
        {
            "key": "ra_one_vs_many",
            "name": "Adequacy: one year vs many",
            "lesson": "A single weather year understates tail risk: LOLE and EUE "
                      "rise sharply once many correlated weather years (with "
                      "dunkelflaute coincident with peak load) are sampled.",
            "a": ra("ra_one", weather_years=[0]),
            "b": ra("ra_many", weather_years=list(range(10))),
        },
        {
            "key": "pf_nodal_vs_zonal",
            "name": "AC power flow: nodal vs zonal dispatch",
            "lesson": "An economically optimal dispatch can violate AC physics. "
                      "A transport (zonal) dispatch ignores intra-zonal lines, so "
                      "the AC power flow on the full network shows different "
                      "losses and overloads than a nodal-feasible dispatch; both "
                      "expose losses the DC model omitted and N-1 violations.",
            "a": pf("pf_nodal", pf_dispatch_mode="nodal"),
            "b": pf("pf_zonal", pf_dispatch_mode="zonal"),
        },
        {
            "key": "dyn_inertia",
            "name": "Frequency: high vs low inertia",
            "lesson": "As synchronous inertia is displaced by inverters, the "
                      "frequency nadir deepens and the RoCoF worsens after losing "
                      "the largest unit — and a minimum-inertia / FFR requirement "
                      "flows back up into operations and planning (Section 6.7).",
            "a": dyn("dyn_high_inertia", dyn_inertia_scale=1.0),
            "b": dyn("dyn_low_inertia", dyn_inertia_scale=0.3),
        },
        {
            "key": "dyn_ffr",
            "name": "Low inertia: with vs without FFR",
            "lesson": "Fast frequency response arrests the frequency decline that "
                      "low inertia causes, lifting the nadir even though the "
                      "initial RoCoF is unchanged.",
            "a": dyn("dyn_noffr", dyn_inertia_scale=0.3, dyn_enable_ffr=False),
            "b": dyn("dyn_ffr", dyn_inertia_scale=0.3, dyn_enable_ffr=True,
                     dyn_ffr_mw=400.0),
        },
        {
            "key": "emt_strong_vs_weak",
            "name": "EMT: strong vs weak grid (SCR)",
            "lesson": "On the dynamics-flagged inverter pocket, a grid-following "
                      "converter is well-damped on a strong grid but suffers a "
                      "control-driven oscillatory instability on a weak grid (low "
                      "SCR) — a fast dynamic the RMS phasor model declared stable.",
            "a": emt("emt_strong", emt_scr_override=5.0, emt_pll_bw_hz=30.0),
            "b": emt("emt_weak", emt_scr_override=1.2, emt_pll_bw_hz=30.0),
        },
    ]


# --- oracle round-trips: transparent kernel vs mature library (Section 11) ---


# --- build mode (issue #28): edit the world's proposals from the map --------

# per-technology defaults for user-placed proposals (mirrors the reference
# world's economics so hand-placed options compete on a level field)
_BUILD_DEFAULTS: dict = {
    "ccgt": dict(build_max_mw=500.0, capex_per_mw=1.1e6, fom_per_mw_yr=33_000.0,
                 lifetime_yr=30, fuel_id="gas", heat_rate_mmbtu_per_mwh=6.7,
                 vom_per_mwh=3.0, p_min_pu=0.4),
    "wind": dict(build_max_mw=400.0, capex_per_mw=1.3e6, fom_per_mw_yr=26_000.0,
                 lifetime_yr=25, vom_per_mwh=0.5),
    "solar_pv": dict(build_max_mw=400.0, capex_per_mw=0.9e6, fom_per_mw_yr=18_000.0,
                     lifetime_yr=25, vom_per_mwh=0.5),
    "battery": dict(build_max_mw=300.0, capex_per_mw=240_000.0,
                    capex_per_mwh=180_000.0, duration_h=4.0,
                    fom_per_mw_yr=6_000.0, lifetime_yr=15,
                    efficiency_charge=0.94, efficiency_discharge=0.94,
                    vom_per_mwh=1.0),
    "line": dict(build_max_mw=500.0, capex_per_mw=150_000.0, lifetime_yr=40,
                 reactance_pu=0.11),
}


class PlaceCandidateRequest(BaseModel):
    technology: str  # ccgt | wind | solar_pv | battery | line
    bus_id: Optional[str] = None
    from_bus_id: Optional[str] = None
    to_bus_id: Optional[str] = None
    build_max_mw: Optional[float] = None


@app.post("/api/world/candidates")
def place_candidate(req: PlaceCandidateRequest):
    """Create a build proposal (ExpansionCandidate) from the map.

    VRE proposals borrow the availability profile of the nearest existing
    weather site of the same kind — same regional weather, honestly labeled.
    """
    from ..schema import CandidateKind, ExpansionCandidate

    w = service.world
    if req.technology not in _BUILD_DEFAULTS:
        raise HTTPException(400, f"unknown technology {req.technology}")
    defaults = dict(_BUILD_DEFAULTS[req.technology])
    if req.build_max_mw:
        defaults["build_max_mw"] = req.build_max_mw
    kind = (CandidateKind.LINE if req.technology == "line"
            else CandidateKind.STORAGE if req.technology == "battery"
            else CandidateKind.GENERATOR)
    if kind == CandidateKind.LINE:
        if not (req.from_bus_id and req.to_bus_id):
            raise HTTPException(400, "a line proposal needs from_bus_id and to_bus_id")
    elif not req.bus_id:
        raise HTTPException(400, "a plant proposal needs bus_id")

    n = 1 + sum(1 for c in w.expansion_candidates if c.id.startswith("user_"))
    cid = f"user_{req.technology}_{n}"

    profile = None
    if req.technology in ("wind", "solar_pv"):
        target_kind = "wind" if req.technology == "wind" else "solar"
        bus = next((b for b in w.buses if b.id == req.bus_id), None)
        sites = [st for st in w.weather_sites if st.kind == target_kind]
        if bus and sites:
            nearest = min(sites, key=lambda st: (st.x - bus.x) ** 2 + (st.y - bus.y) ** 2)
            profile = f"availability__{nearest.id}"

    def busname(bid):
        b = next((b2 for b2 in w.buses if b2.id == bid), None)
        return (b.name or bid) if b else bid

    name = (f"{busname(req.from_bus_id)}–{busname(req.to_bus_id)} line (your proposal)"
            if kind == CandidateKind.LINE
            else f"{req.technology} @ {busname(req.bus_id)} (your proposal)")
    cand = ExpansionCandidate(
        id=cid, name=name, kind=kind, technology=req.technology,
        bus_id=req.bus_id, from_bus_id=req.from_bus_id, to_bus_id=req.to_bus_id,
        availability_profile_id=profile, **defaults)
    w.expansion_candidates.append(cand)
    return {"created": cid, "name": name,
            "availability_profile_id": profile,
            "note": (None if kind == CandidateKind.LINE or profile or
                     req.technology not in ("wind", "solar_pv")
                     else "no weather site found — treated as firm capacity")}


@app.delete("/api/world/candidates/{cid}")
def delete_candidate(cid: str):
    w = service.world
    before = len(w.expansion_candidates)
    w.expansion_candidates = [c for c in w.expansion_candidates if c.id != cid]
    if len(w.expansion_candidates) == before:
        raise HTTPException(404, f"no candidate {cid}")
    return {"deleted": cid}


@app.post("/api/world/reset")
def reset_world():
    """Discard in-memory edits; reload the saved world from disk."""
    service.reset()
    return {"ok": True}


@app.get("/api/weather/events")
def weather_events():
    """Named stress/showcase events auto-detected from the ensemble (#34)."""
    from ..weather.events import detect_events

    return detect_events(service.world)


@app.get("/api/oracle/availability")
def oracle_availability():
    """Which oracle libraries are importable in this environment."""
    from ..validation.oracles import available

    return available()


class OracleRequest(BaseModel):
    """Oracle round-trips can validate the *scenario the user ran* (issue #13):
    overrides are applied to the world before both sides solve."""

    scenario: Optional[Scenario] = None
    hour: Optional[int] = None
    weather_year: int = 0
    dispatch_mode: str = "nodal"


def _oracle_world(req: Optional[OracleRequest]):
    from ..scenario import apply_overrides

    w = service.world
    note = None
    if req and req.scenario and req.scenario.overrides:
        w = apply_overrides(w, req.scenario.overrides)
        note = f"validating scenario '{req.scenario.id}' ({len(req.scenario.overrides)} overrides applied)"
    return w, note


def _excluded_assets(w) -> dict:
    """What the oracle translation does NOT model — reported so a MATCH verdict
    is never mistaken for coverage (issues #14/#16)."""
    out = {}
    if w.storage_units:
        out["storage_units"] = [st.id for st in w.storage_units]
    if w.dc_lines:
        out["dc_lines"] = [d.id for d in w.dc_lines]
    if w.expansion_candidates:
        out["expansion_candidates"] = len(w.expansion_candidates)
    if w.resource_potentials:
        out["resource_potentials"] = len(w.resource_potentials)
    taps = [t.id for t in w.transformers if abs(t.tap_ratio - 1.0) > 1e-9
            or abs(t.phase_shift_deg) > 1e-9]
    if taps:
        out["transformers_with_taps"] = taps
    return out


def _peak_mw(w, weather_year: int) -> float:
    store = w.time_series_store
    total = None
    for ld in w.loads:
        if ld.demand_profile_id and ld.demand_profile_id in store:
            arr = store.get(ld.demand_profile_id)
            total = arr if total is None else total + arr
    return float(total.max()) if total is not None else 1000.0


def _run_oracle_powerflow(req: Optional[OracleRequest]):
    from ..engines.powerflow import peak_load_hour
    from ..validation.oracles.pandapower_oracle import HAVE_PANDAPOWER, compare_power_flow

    if not HAVE_PANDAPOWER:
        return {"available": False, "oracle": "pandapower"}
    w, note = _oracle_world(req)
    year = req.weather_year if req else 0
    h = (req.hour if req and req.hour is not None else peak_load_hour(w, year))
    mode = req.dispatch_mode if req else "nodal"
    # tolerances scale with system size instead of fixed absolutes (issue #15)
    mw_tol = max(1.0, 0.001 * _peak_mw(w, year))
    try:
        cmp = compare_power_flow(w, h, year, dispatch_mode=mode)
    except Exception as exc:
        # divergence is a first-class, explained outcome — not a 500 (issue #15)
        return {
            "available": True, "oracle": "pandapower", "engine": "pf", "hour": h,
            "converged_both": False, "failure": str(exc),
            "why": ("One side failed to converge or errored. Common causes: an "
                    "operating point outside voltage limits (heavy scenario "
                    "overrides), an islanded bus after a retirement, or an "
                    "oracle translation gap (see excluded assets)."),
            "excluded": _excluded_assets(w), "note": note,
        }
    return {
        "available": True, "oracle": "pandapower", "engine": "pf", "hour": h,
        "note": note,
        "metrics": [
            {"name": "max |V| difference", "kernel": "—", "oracle": "—",
             "diff": cmp.max_v_diff_pu, "unit": "pu", "tol": 1e-4,
             "why": ("Bus voltage magnitudes from two independent Newton-Raphson "
                     "implementations should agree to numerical precision; a gap "
                     "means the two sides solved different networks.")},
            {"name": "max angle difference", "kernel": "—", "oracle": "—",
             "diff": cmp.max_angle_diff_deg, "unit": "deg", "tol": 1e-2,
             "why": "Angles are relative to the slack; both sides use the same reference."},
            {"name": "max branch-flow difference", "kernel": "—", "oracle": "—",
             "diff": cmp.max_flow_diff_mw, "unit": "MW", "tol": mw_tol,
             "why": ("Flows follow from voltages and impedances. Tolerance is "
                     "0.1% of system peak load, not a fixed MW.")},
            {"name": "total losses", "kernel": cmp.losses_glassbox_mw,
             "oracle": cmp.losses_pandapower_mw,
             "diff": abs(cmp.losses_glassbox_mw - cmp.losses_pandapower_mw),
             "unit": "MW", "tol": mw_tol,
             "why": ("I2R losses are the most sensitive aggregate — they amplify "
                     "any small voltage/flow disagreement.")},
        ],
        "converged_both": cmp.converged_both, "n_buses": cmp.n_buses,
        "excluded": _excluded_assets(w),
    }


@app.get("/api/oracle/powerflow")
def oracle_powerflow(hour: Optional[int] = None, weather_year: int = 0,
                     dispatch_mode: str = "nodal"):
    """AC power flow: hand-built Newton-Raphson vs pandapower (Section 6.5)."""
    return _run_oracle_powerflow(OracleRequest(hour=hour, weather_year=weather_year,
                                               dispatch_mode=dispatch_mode))


@app.post("/api/oracle/powerflow")
def oracle_powerflow_scenario(req: OracleRequest):
    return _run_oracle_powerflow(req)


def _run_oracle_dispatch(req: Optional[OracleRequest]):
    from ..engines.powerflow import peak_load_hour
    from ..validation.oracles.pypsa_oracle import HAVE_PYPSA, compare_dispatch

    if not HAVE_PYPSA:
        return {"available": False, "oracle": "pypsa"}
    w, note = _oracle_world(req)
    year = req.weather_year if req else 0
    h = (req.hour if req and req.hour is not None else peak_load_hour(w, year))
    mw_tol = max(1.0, 0.001 * _peak_mw(w, year))
    try:
        cmp = compare_dispatch(w, h, year)
    except Exception as exc:
        return {
            "available": True, "oracle": "PyPSA", "engine": "pcm", "hour": h,
            "converged_both": False, "failure": str(exc),
            "why": ("One side failed to solve. If a scenario override retired "
                    "capacity or scaled load, the copper-plate problem may be "
                    "infeasible on the oracle side (it has no unserved-energy "
                    "variable)."),
            "excluded": _excluded_assets(w), "note": note,
        }
    return {
        "available": True, "oracle": "PyPSA", "engine": "pcm", "hour": h,
        "note": note,
        "metrics": [
            {"name": "objective", "kernel": cmp.objective_glassbox,
             "oracle": cmp.objective_pypsa, "diff": cmp.objective_rel_diff,
             "unit": "rel", "tol": 1e-4,
             "why": ("Same copper-plate merit-order problem solved by two "
                     "independent formulations — the optimal cost must agree.")},
            {"name": "max per-generator dispatch difference", "kernel": "—",
             "oracle": "—", "diff": cmp.max_dispatch_diff_mw, "unit": "MW",
             "tol": mw_tol,
             "why": ("Individual setpoints can differ when units tie on marginal "
                     "cost (degenerate optima) even though total cost matches.")},
            {"name": "total dispatched", "kernel": cmp.total_dispatch_glassbox_mw,
             "oracle": cmp.total_dispatch_pypsa_mw,
             "diff": abs(cmp.total_dispatch_glassbox_mw - cmp.total_dispatch_pypsa_mw),
             "unit": "MW", "tol": mw_tol,
             "why": "Both sides must serve the same load in a lossless copper plate."},
        ],
        # a MATCH verdict covers ONLY the translated subset (issue #14):
        "excluded": _excluded_assets(w),
        "scope_note": ("This oracle compares a single-hour, copper-plate "
                       "thermal+VRE+hydro dispatch. Storage, network limits, "
                       "unit commitment, and investment are not exercised."),
    }


@app.get("/api/oracle/dispatch")
def oracle_dispatch(hour: Optional[int] = None, weather_year: int = 0):
    """Economic dispatch: transparent linopy core vs PyPSA LOPF (Sections 6.2/6.3)."""
    return _run_oracle_dispatch(OracleRequest(hour=hour, weather_year=weather_year))


@app.post("/api/oracle/dispatch")
def oracle_dispatch_scenario(req: OracleRequest):
    return _run_oracle_dispatch(req)


@app.get("/api/oracle/dynamics")
def oracle_dynamics():
    """RMS swing: transparent SMIB integrator vs Andes (Section 6.6)."""
    from ..validation.oracles.andes_oracle import HAVE_ANDES, compare_swing_frequency

    if not HAVE_ANDES:
        return {"available": False, "oracle": "andes"}
    cmp = compare_swing_frequency()
    return {
        "available": True, "oracle": "Andes", "engine": "dyn",
        "metrics": [
            {"name": "swing frequency (kernel vs Andes)", "kernel": cmp.glassbox_hz,
             "oracle": cmp.andes_hz, "diff": cmp.rel_diff_glassbox_vs_andes,
             "unit": "rel", "tol": 0.08},
            {"name": "swing frequency (Andes vs analytic)", "kernel": cmp.analytic_hz,
             "oracle": cmp.andes_hz, "diff": cmp.rel_diff_andes_vs_analytic,
             "unit": "rel", "tol": 0.05},
        ],
        "detail": {"andes_hz": cmp.andes_hz, "glassbox_hz": cmp.glassbox_hz,
                   "analytic_hz": cmp.analytic_hz},
    }


# --- serve the built frontend (single-port deployment) ----------------------
# When the React app has been built (frontend/dist), serve it from the same
# server so the whole tool runs on one port with no proxy/CORS configuration.
# This makes Codespaces / Replit / any cloud runner a single forwarded port.

_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
