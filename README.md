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

On top of that planning stack sits a seventh layer — **Ops Mode**, an
interactive control-room simulator (`glassbox/rtops/`) that operates the exact
system the planning views built. It has its own PRD (`docs/prd_ops_mode.md`,
issue #56) and is summarized in its own section below.

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

## Ops Mode — the control room (`glassbox/rtops/`)

A distinct seventh layer: instead of planning the system over decades, you
**operate it for one shift** (a 12-hour day at 5-minute resolution) and are
graded the way NERC grades real control desks. Built across issues #56 (PRD +
Phases 0–2), #57 (economics + clearances), and #58 (Phase 3); ~40 dedicated
tests. The full design and rationale — including the research grounding
(Grid2Op, NREL Sienna PowerSimulations, real NERC standards, CIM/CGMES) — is in
`docs/prd_ops_mode.md`.

What it is, top to bottom:

- **A CIM-aligned node-breaker substation layer** (`schema/substation.py`,
  `rtops/elaborate.py`, `rtops/topology.py`). Every planning bus elaborates into
  a `Substation` of busbar sections, connectivity nodes, and switches (breakers
  + disconnectors) mirroring IEC 61970-301 class names. A power-flow "bus"
  (`TopologicalNode`) is **computed** by collapsing connectivity nodes across
  closed switches — the CIM lesson, executable. **CI-proven invariant:** with
  every switch closed the derived bus-branch model is byte-identical to the
  original, so the layer is invisible to the planning engines.
- **A staged simulation kernel** (`rtops/kernel.py`) in the NREL Sienna
  PowerSimulations shape: day-ahead unit commitment → real-time SCED (5-min,
  commitment fixed as a feedforward, plus a storage-SOC feedforward) → AGC/ACE
  emulation → DC network + Grid2Op-style protection (soft/hard overflow trips
  through real bay breakers). Seeded actuals diverge from the forecast; **same
  seed ⇒ byte-identical replay** (CI-tested).
- **NERC-real scoring** (`rtops/scoring.py`): CPS1, BAAL (30-consecutive-minute
  rule), DCS (15-min recovery), TOP-001 SOL clocks — real formulas, every
  simplification a named constant. A simulated Reliability Coordinator walks the
  EEA emergency ladder and runs an RTCA N-1 screen.
- **Market economics**: nodal LMPs from the RT LP's balance duals, and an
  ORDC-lite stepped reserve demand curve so scarcity prices *before* load is
  shed.
- **The trust layer** (`rtops/telemetry.py`): synthesized SCADA (noise,
  dropout, lying meters) feeding a real DC weighted-least-squares **state
  estimator** with largest-normalized-residual bad-data detection. The operator
  dashboard runs on the *estimate*, not the truth — the ops-vs-planning schema
  thesis made visceral.
- **Operator craft**: switching orders (breakers-then-disconnectors, interlocks
  enforced), clearances gated on verified isolation, stuck-breaker → bus-section
  clearing, HRUC commitment approvals, reactive (AVR) dispatch with periodic AC
  voltage-schedule checks, a restoration epilogue after blackout.
- **UI**: a **Control Room** tab (server-held session, lazily-advanced clock,
  the always-on EMS dashboard, action bar with self-explaining rejections) and a
  seven-scenario graded library (tutorial → morning ramp → DCS drill → 30-minute
  clock → switching order → blackout/restoration → storm shift).

Still open (tracked in issue #58): full AC voltage-VAR as a first-class layer,
per-`EquipmentTerminal` telemetry binding, and the cross-repo interoperability
experiments (see the companion tool below).

### Companion tool: `grid-rosetta`

A separate repository (`dpaolella/grid-rosetta`) is a hub-and-spoke translation
test bench that measures what is lost when a grid model is translated between
schemas (PyPSA, NREL Sienna, Glassbox, and — planned — a CGMES/CIM spoke). It
consumes Glassbox as a library. The remaining Ops Mode interoperability work
(round-tripping an operations-bearing world through each hub, and validating a
CGMES import against ENTSO-E's MiniGrid conformity model) lives at the seam
between the two repos and needs both in scope. See `docs/sienna_comparison.md`
here for the written schema comparison that motivates it.

## Status

The planning stack (six engines) runs on the default system; Ops Mode adds the
control-room layer on top. Validated by **165 passing tests** (`pytest`,
excluding the dev-only oracle round-trips which need optional libraries).

The planning stack has two complementary validation layers (Section 11):

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
  rtops/       # Ops Mode: node-breaker substations, topology processing,
               #   the shift kernel, NERC scoring, telemetry + state estimation,
               #   switching/clearances, scenario library (issues #56/#57/#58)
  scenario/    # scenario object + run orchestration + diffing
  validation/  # oracle round-trips, canonical cases, phenomena checklists
  api/         # FastAPI app: world, fields by facet, time series, explain payloads
  frontend/    # React + TypeScript: network canvas, layer-filtered inspector, plots
  tests/
data/          # serialized default world + generated weather artifacts
docs/          # prd_ops_mode.md (Ops Mode design), sienna_comparison.md
               #   (schema comparison incl. CIM/CGMES), schema_atlas.html
```

### Where to look (orientation for a new contributor or session)

- **Planning engines**: `glassbox/engines/*.py` — one per rung, each with
  `explain()`. Start from `economic_core.py` (the shared CEM/PCM formulation).
- **Ops Mode**: `glassbox/rtops/` — start from `kernel.py` (the shift loop) and
  `docs/prd_ops_mode.md` (the design). The Control Room UI is
  `frontend/src/components/ControlRoomPanel.tsx`; its API is the
  `/api/opsim/*` endpoints in `glassbox/api/app.py`.
- **Schema**: `glassbox/schema/` — `entities.py` (planning), `substation.py`
  (node-breaker ops layer), `facets.py` (the facet-tagging convention).
- **Open work**: GitHub issues #47–#52 (planning-view UX papercuts), #58 (Ops
  Mode Phase 3 remainder + the grid-rosetta interop experiments).
- **Companion repo**: `dpaolella/grid-rosetta` — the schema-translation bench.

### Core design invariants (Section 2)

- **One stored world, many views.** Store fine (positive-sequence nodal, full
  multi-year chronology), derive coarse via three projection operators.
- **Transparency contract.** Every engine and operator implements `explain()`,
  surfacing its formulation, concrete inputs/outputs, and diagnostic
  intermediates.
- **Facet-tagged schema.** Every field is tagged with the modeling layer(s) that
  consume it. This is machine-readable and drives both the attribute operator
  and the layer-filtered inspector.
- **Investment vs operations (Sienna-style).** Existing physical assets
  (`Generator`, `Storage`, …) carry a `status` lifecycle, not an `is_candidate`
  flag. Buildable options are a separate `ExpansionCandidate` entity (siting,
  build limits / resource potential, capex, operating template) that only the
  capacity-expansion layer sees and that the CEM materializes when it builds.
  The map renders these as a **Resource Potential** overlay on the `inv` layer.

## Run it

### In your browser, no local install — GitHub Codespaces

1. On the GitHub repo page: **Code ▸ Codespaces ▸ Create codespace on
   `claude/build-to-spec-uuw601`**.
2. Wait for the container to build (it installs deps, builds the world, and
   builds the UI automatically via `.devcontainer`).
3. In the Codespace terminal run **`bash scripts/run.sh`**.
4. When the **port 8000** notification appears, click **Open in Browser**.

That's the whole app — UI and API — on one URL. (Codespaces' free monthly hours
cover this; Gitpod or Replit work the same way by importing the repo.)

### Locally — one command

The backend serves the built UI, so it's a single server on one port:

```bash
pip install -e .          # Python 3.11+
bash scripts/run.sh       # builds the world + UI on first run, then serves
# open http://localhost:8000
```

For the **Oracles** tab (kernel-vs-library validation), also install the
dev-only oracle libraries: `pip install -e ".[oracles]"`.

### Local dev mode (hot reload)

```bash
pip install -e .
python scripts/build_default_world.py
uvicorn glassbox.api.app:app --reload          # API on :8000
cd frontend && npm install && npm run dev        # UI on :5173 (proxies /api)
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
