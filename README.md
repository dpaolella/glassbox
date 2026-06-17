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

All six phases of the PRD (Section 12) are implemented: the full abstraction
ladder runs on one stored world, every engine and operator implements
`explain()`, and each Section 1.3 learning objective maps to a concrete in-tool
demonstration (see `glassbox/validation/phenomena.py`).

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

**Phase 2 — Resource adequacy** (done): a sequential Monte Carlo engine
(`engines/adequacy.py`) sampling correlated weather years and two-state
(MTTF/MTTR) forced outages, with chronological storage dispatch vectorized
across draws. Computes LOLE, EUE, and ELCC (effective load carrying capability
via the standard iterative method with common random numbers). Demonstrates the
phenomena (Section 11.3): a single weather year understates tail risk, VRE ELCC
is small and declines with penetration, and storage ELCC grows with duration.
Validated against the analytical binomial-convolution LOLP for a small case
(Section 11.2). Exposed as an `ra` scenario layer with a one-year-vs-many preset.

**Phase 3 — Steady-state security** (done): a hand-built AC Newton-Raphson power
flow (`engines/powerflow.py`) with the admittance matrix, power-mismatch vector,
Jacobian and iteration trace all exposed in `explain()`, plus N-1 contingency
screening and a DC/PTDF power flow. The operating point comes from a single-hour
economic dispatch (the PCM→power-flow handoff): a nodal-feasible dispatch
converges and reveals the losses the DC model omits, while a transport (zonal)
dispatch exposes intra-zonal overloads the aggregate model never saw. N-1 outages
produce post-contingency violations. Validated against an analytical 2-bus case
(Section 11.2). Exposed as a `pf` scenario layer with a nodal-vs-zonal preset.

**Phase 4 — Dynamics** (done): RMS/phasor stability (`engines/dynamics.py`) with
two transparent models integrated by hand (RK4). An aggregated System Frequency
Response model (swing + governor + fast frequency response) shows the frequency
nadir deepen and the RoCoF worsen as synchronous inertia is displaced by
inverters, that FFR arrests the decline, and that grid-forming converters
provide effective inertia grid-following ones do not. A Single-Machine-Infinite-
Bus model shows a longer fault-clearing time breaking transient stability, with
the critical clearing time validated against the analytical equal-area criterion
(Section 11.2). The dynamics→operations handoff (Section 6.7) turns the result
into a minimum-inertia / RoCoF requirement and an FFR reserve that flow upward.
Exposed as a `dyn` scenario layer with high-vs-low-inertia and FFR presets.

**Phase 5 — EMT and resonance** (done): micro-examples on the dynamics-flagged
weak pocket (`engines/emt.py`). A short-circuit-ratio screen (from the bus
impedance matrix Z = Y⁻¹) selects the weakest inverter pocket (the RMS→EMT
handoff). An impedance/admittance frequency scan locates the LCL resonance,
validated against the analytical resonance frequency. A grid-following converter
with a PLL and constant-power current loop is integrated in the dq frame on a
Thévenin grid: it is well-damped on a strong grid but suffers a control-driven
oscillatory instability on a weak grid (low SCR) — a fast dynamic the RMS phasor
model declared stable. Exposed as an `emt` scenario layer with a
strong-vs-weak-grid preset and an impedance-scan plot in the UI.

## Status

All six engines run on the default system, validated by **97 passing tests**.

Two complementary validation layers (Section 11):

- **Analytical / canonical** (always on): binomial-convolution LOLP (RA), the
  2-bus power flow, the equal-area critical clearing time (dynamics), and the LCL
  resonance frequency (EMT).
- **Oracle round-trips** (`glassbox/validation/oracles/`, dev-only, auto-skipped
  if the library is absent): the hand-built AC power flow matches **pandapower**
  to machine precision (max ΔV = 0, identical losses); the economic dispatch
  matches **PyPSA** LOPF to machine precision (objective and per-generator
  dispatch); and the swing dynamics match **Andes** — the rotor-angle oscillation
  frequency agrees with Andes and the analytical linearized swing within damping.
  RA and EMT have no mature oracle, so they rest on the analytical cases above.

Install the oracle libraries with `pip install -e ".[oracles]"`. The Scenario
Lab ships six demonstration presets, one per lesson, and the **Oracles** tab runs
the kernel-vs-library round-trips live (per-engine MATCH/DIVERGES verdicts with
the metric differences against tolerance).

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
