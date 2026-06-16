# Glassbox

An inspectable, multi-paradigm grid-modeling sandbox: **one fully inspectable
synthetic power system** ("a world"), and a set of solvers that read that one
world at different timescales and fidelities. The unifying object is the data
model, not any single engine — the same physical system is abstracted
differently by each modeling paradigm, and seeing those differences is the
lesson.

The abstraction ladder, coarsest to finest:

1. **Capacity expansion (CEM)** — decades, "what do we build?"
2. **Production cost (PCM)** — a year, hourly, "how does it run and what does it cost?"
3. **Resource adequacy (RA)** — many draws, "does it keep the lights on?"
4. **Steady-state security (power flow + N-1)** — a snapshot, "does it physically flow within limits?"
5. **Dynamic stability (RMS / phasor)** — seconds, "does it stay synchronized?"
6. **Electromagnetic transients (EMT)** — microseconds, "what happens in the wires?"

This repository is built in the phase order of the PRD (Section 12).

**Phase 0 — The world** (done): the schema spine, the synthetic weather
generator, the parametrized default seed system, the three projection operators,
and a backend API + React frontend for inspecting the static world.

**Phase 1 — Economic layers** (done): the capacity-expansion (CEM) and
production-cost (PCM) engines on a shared transparent linopy/HiGHS formulation,
the scenario framework with overrides and diffing, and a frontend Scenario Lab
that runs the canonical demonstration pairs (nodal vs zonal, one year vs many,
carbon price vs none) and renders each engine's `explain()` math. CEM
co-optimizes investment + operations over representative periods; PCM does
chronological MILP unit commitment + LP economic dispatch with DC power flow and
LMPs from a fixed-commitment price pass.

## Architecture (Section 3.1)

```
glassbox/
  schema/      # Pydantic v2 models, facet enum + metadata, units, World, results
  world/       # parametrized default seed system, serialization
  weather/     # synthetic multi-year weather generator (known ground truth)
  operators/   # spatial, temporal, attribute projection operators
  engines/     # one engine per modeling rung (Phases 1-5)
  scenario/    # scenario object + run orchestration + diffing
  validation/  # oracle round-trips, canonical cases, phenomena checklists
  api/         # FastAPI app: world, fields by facet, time series, explain payloads
  frontend/    # React + TypeScript: network canvas, layer-filtered inspector, plots
  tests/
data/          # serialized default world + generated weather artifacts
```

### Core design invariants (Section 2)

- **One stored world, many views.** Store fine (positive-sequence nodal, full
  multi-year chronology), derive coarse via three projection operators.
- **Transparency contract.** Every engine and operator implements `explain()`,
  surfacing its formulation, concrete inputs/outputs, and diagnostic
  intermediates.
- **Facet-tagged schema.** Every field is tagged with the modeling layer(s) that
  consume it. This is machine-readable and drives both the attribute operator
  and the layer-filtered inspector.

## Quick start

```bash
# 1. Install (Python 3.11+)
pip install -e .

# 2. Build the default world + multi-year weather (writes data/default_world/)
python scripts/build_default_world.py

# 3. Run the backend API
uvicorn glassbox.api.app:app --reload

# 4. Run the frontend (separate terminal)
cd frontend && npm install && npm run dev
```

## Tests

```bash
pytest
```

Phase 0 tests cover schema/facet introspection, the weather generator's
ground-truth phenomena (wind-load anticorrelation, inter-annual spread, spatial
correlation), the projection operators, default-system properties, and
serialization round-trips. Oracle round-trips (PyPSA, pandapower, Andes) land
with their engines in later phases.
