"""End-to-end tests: the experiments the bench exists to run.

Each test is one of the claims from the "PyPSA as hub" debate, made executable:

  1. IEEE case -> pypsa hub -> glassbox: the translated world is a valid,
     ENGINE-SOLVABLE glassbox world (the "open in the app" demo).
  2. glassbox -> pypsa hub -> glassbox: reserves/policies/zones survive ONLY
     via the sidecar, and the manifest says so (parked then restored).
  3. glassbox -> sienna hub -> glassbox: reserves survive by TRANSLATION
     (Sienna holds them natively) — same outcome, different story, visible
     in the manifests.
  4. Typeless source (IEEE case) -> sienna: every generator is a counted
     manual-mapping event (the closed-taxonomy cost, measured).
  5. Unknown free-text carrier -> closed enum: counted, not silent.
"""

from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore")

pypsa = pytest.importorskip("pypsa")
pytest.importorskip("pandapower")
glassbox = pytest.importorskip("glassbox")

from rosetta import load, merged_manifest, translate  # noqa: E402
from rosetta.core import Payload  # noqa: E402


def _cov_total(payload, key):
    return sum(len(getattr(c, key)) for c in payload.coverage)


# --- 1. the flagship: IEEE case through the pypsa hub into the app ----------


def test_ieee14_via_pypsa_hub_opens_and_solves_in_glassbox(tmp_path):
    p = load("matpower", "case14")
    p = translate(p, "glassbox", hub="pypsa", opts={"hours": 24})
    world = p.native

    assert p.schema == "glassbox"
    assert len(world.buses) == 14
    assert world.reference_bus_id in {b.id for b in world.buses}
    assert world.loads and all(ld.demand_profile_id for ld in world.loads)

    # it round-trips through glassbox's own persistence ...
    from glassbox.world import load_world, save_world
    save_world(world, tmp_path / "w")
    reloaded = load_world(tmp_path / "w")
    assert len(reloaded.buses) == 14

    # ... and the app's economic engine can actually SOLVE it
    import numpy as np
    from glassbox.engines import assemble_view
    from glassbox.engines.economic_core import (EngineOptions,
                                                build_dispatch_model,
                                                solve_model)
    from glassbox.operators.spatial import SpatialMode, SpatialProjection

    sview = SpatialProjection(SpatialMode.IDENTITY).apply(reloaded)
    T = 24
    view = assemble_view(reloaded, sview, np.arange(T),
                         np.zeros(T, dtype=int), np.ones(T), 1.0,
                         investment=False)
    built = build_dispatch_model(view, EngineOptions(
        investment=False, unit_commitment=False, reserves=False, label="t"))
    status = solve_model(built)
    assert "ok" in status or "optimal" in status.lower()
    unserved = float(built.m.variables["unserved"].solution.values.sum())
    assert unserved < 1.0, "translated system should serve its own load"


# --- 2 & 3. reserves through both hubs: sidecar vs native translation -------


@pytest.fixture(scope="module")
def default_world_payload():
    from glassbox.world import build_default_world_with_weather
    w, _ = build_default_world_with_weather()
    return w


def test_reserves_survive_pypsa_hub_only_via_sidecar(default_world_payload):
    w = default_world_payload
    n_reserves = len(w.reserve_products)
    assert n_reserves > 0, "default world should carry a reserve product"

    p = Payload("glassbox", w)
    p = translate(p, "glassbox", hub="pypsa", opts={"hours": 48})
    back = p.native

    # concept survived ...
    assert len(back.reserve_products) == n_reserves
    assert {r.id for r in back.reserve_products} == \
           {r.id for r in w.reserve_products}
    # ... but the manifest shows HOW: parked on the way in, restored on the
    # way out — PyPSA itself never held it
    parked = [x for c in p.coverage for x in c.parked
              if x["concept"] == "glassbox:reserve_products"]
    restored = [x for c in p.coverage for x in c.restored
                if x["concept"] == "glassbox:reserve_products"]
    assert parked and restored

    # zones ride the same mechanism (PyPSA buses have no zone membership)
    assert {z.id for z in back.zones} == {z.id for z in w.zones}
    # capacities survive translation proper (not the sidecar)
    cap = {g.id: g.p_max_mw for g in w.generators if g.in_service}
    cap_back = {g.id: g.p_max_mw for g in back.generators}
    for gid, mw in cap.items():
        assert abs(cap_back[gid] - mw) < 1e-6


def test_reserves_survive_sienna_hub_by_translation(default_world_payload):
    p = Payload("glassbox", default_world_payload)
    p = translate(p, "glassbox", hub="sienna", opts={"hours": 48})
    back = p.native

    assert len(back.reserve_products) == \
           len(default_world_payload.reserve_products)
    # NOT parked as glassbox:reserve_products this time — translated natively
    parked = [x for c in p.coverage for x in c.parked
              if x["concept"] == "glassbox:reserve_products"]
    assert not parked
    # ... though percentage rules were flattened, and the ledger says so
    approx = [x for c in p.coverage for x in c.approximated
              if "reserve rule" in x.get("what", "")]
    assert approx, "pct-based reserve rules should be flagged as flattened"


# --- 4. the typeless-source experiment ---------------------------------------


def test_typeless_ieee_case_into_sienna_counts_manual_mappings():
    p = load("matpower", "case14")
    p = translate(p, "sienna")
    n_gens = len(p.native.thermal)
    assert n_gens >= 5  # 4 gens + slack
    manual = [x for c in p.coverage for x in c.manual_mapping_required]
    # EVERY typeless generator is a counted mapping debt at the closed door
    assert len(manual) == n_gens


def test_typeless_cost_deferred_by_pypsa_hub_not_erased():
    """Through the open hub the same debt appears one leg LATER (at glassbox's
    closed enum) instead of at the hub door — deferred, not erased."""
    via_pypsa = translate(load("matpower", "case14"), "glassbox", hub="pypsa",
                          opts={"hours": 24})
    manual = [x for c in via_pypsa.coverage for x in c.manual_mapping_required]
    assert manual, "carrier-less generators still need mapping at the spoke"
    # and the manifest attributes it to the glassbox leg, not the pypsa leg
    by_bridge = {c.bridge: len(c.manual_mapping_required)
                 for c in via_pypsa.coverage}
    assert by_bridge.get("matpower->pypsa", 0) == 0
    assert by_bridge.get("pypsa->glassbox", 0) > 0


# --- 5. unknown carriers are counted, never silent ---------------------------


def test_unknown_carrier_is_counted_not_silent(default_world_payload):
    p = Payload("glassbox", default_world_payload)
    p = translate(p, "pypsa", opts={"hours": 24})
    n = p.native
    import pandas as pd
    n.add("Generator", "mystery_unit", bus=n.buses.index[0], p_nom=50.0,
          carrier="unobtainium-chp")
    p2 = translate(p, "sienna")
    manual = [x for c in p2.coverage for x in c.manual_mapping_required]
    assert any(m["entity"] == "mystery_unit" for m in manual)
    mystery = next(t for t in p2.native.thermal if t.name == "mystery_unit")
    assert mystery.prime_mover.value == "OT"


# --- CLI smoke ----------------------------------------------------------------


def test_cli_compare_hubs_runs(capsys):
    from rosetta.cli import main
    rc = main(["compare-hubs", "case14", "--from", "matpower",
               "--to", "glassbox", "--hubs", "pypsa,sienna", "--hours", "24"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pypsa" in out and "sienna" in out
