# Glassbox vs. SiennaGridDB — data-model comparison

A review of [SiennaGridDB's data model](https://github.com/G-PST/data-schema-exercise/blob/main/data_schemas/sienna-griddb_data_model.yaml)
(the NREL Sienna ecosystem schema) and [PyPSA's data model](https://github.com/G-PST/data-schema-exercise/blob/main/data_schemas/pypsa_data_model.yaml)
against Glassbox's, focused on how each one organizes one power system for many
kinds of analysis.

> *As of the current `main` (post oracle-depth / build-mode-v2). Since the first
> cut of this doc the buildable side of the schema grew a second representation
> (zonal supply curves), reserves and cross-layer requirements became
> first-class, the world became editable in place, and **PyPSA was added as a
> third point of comparison** — all reflected below.*

> **Visual companion:** an interactive three-way comparison lives at
> [`docs/schema_atlas.html`](./schema_atlas.html) — the organizing philosophy of
> each schema, a capability-coverage matrix, an entity "Rosetta stone," and how
> far up the physics stack each one reaches. Open it in a browser (it is
> self-contained). The tables below are the written form of the same material.

## TL;DR

Both schemas separate **one physical system** into views for different analyses,
store **time series out-of-line**, attach **explicit per-field units with a base**,
and keep **existing assets distinct from investment options**. The core
difference is the *mechanism* of separation: Sienna splits into **domain
packages** (Core / Operations / Investments / Dynamics) made of distinct
component types; Glassbox keeps **shared component types** and tags **each field
with a facet** (`core/inv/ops/adq/pf/dyn/emt`). Sienna is a production,
multi-language, DB-backed interchange schema; Glassbox is a single-user
pedagogical schema whose facets also drive the UI.

## Where they agree

| Concern | Sienna | Glassbox |
|---|---|---|
| One system, many analyses | 4 domain packages over one core | 7 field-level facets over one `World` |
| Existing vs. buildable | Operations domain vs. **Investments** domain | `Generator`/`Storage`/`ACLine` assets vs. **`ExpansionCandidate`** + **`ResourcePotential`** |
| Investment as supply curve | Investment technologies with build limits | `ResourcePotential` → stepped `SupplyTranche`s (rising $/MW) |
| Reserves / ancillary services | AGC, constant + variable reserves (up/down), reserve groups | `ReserveProduct` (spinning, `pct_load`/`pct_vre`/`fixed_mw` rule) |
| Units | per-field unit metadata + `UnitSystem` enum, base V/power | per-field `unit` + `base` (`system_mva`/`machine_mva`), per-unit derived |
| Time series | HDF5 arrays, referenced from the DB | npz arrays, referenced by id from `TimeSeriesStore` |
| Substrate | Pydantic (among Julia/SQL) | Pydantic v2 |
| Component shape | "flat and self-contained" | flat entities in one container |
| Dynamics | separate Dynamics package | polymorphic `DynamicModel` + `dyn`/`emt` facets |

The existing-vs-candidate refactor brought Glassbox into line with Sienna's
**Operations vs. Investments** split — candidates are their own entities with
build limits and an operating template, not a boolean on the asset. Transmission
is a genuine build decision too (`ExpansionCandidate` of `kind=line`, modeled as
a transport corridor by the CEM), matching Sienna's supply/storage/**and network**
investment scope.

## Where they differ

**1. Separation mechanism — packages vs. facets.**
Sienna achieves modularity at the *component/table* level: a capacity-expansion
tool imports `Core + Investments`; a dynamics tool imports `Core + Dynamics`.
A given physical thing may be represented by different component types across
packages. Glassbox keeps one component type (e.g. `Generator`) and tags each
**field** with the layers that consume it; the attribute projection operator
then slices the same object per layer.
- *Sienna's win:* packages are independently adoptable and avoid a monolithic
  object; cleaner for a multi-tool ecosystem.
- *Glassbox's win:* "one stored world, many views" is literal — the same
  `Generator` reveals heat-rate to `ops`, inertia to `dyn`, etc., which powers
  the layer-filtered inspector and makes the abstraction levels visible *in the
  data*. That's the whole pedagogical point.

**2. Investment representation — two granularities, and a stepped supply curve.**
Glassbox now carries *two* buildable representations, which the earlier version
of this doc predated:
- **`ExpansionCandidate`** — a specific plant, storage unit, or line at a
  specific bus (nodal granularity; "should we build *this* here?").
- **`ResourcePotential`** — the aggregate buildable potential of a technology
  across a whole *zone*, expressed as a **stepped supply curve** of
  `SupplyTranche`s: the best (cheapest, highest-CF) sites are exhausted first, so
  a technology's cost is a *rising* curve of $/MW, not a scalar. The CEM builds
  tranches cheapest-first up to the potential and sites them at the zone's
  interconnection hub.

Sienna's Investments domain covers "supply/storage/demand-side technologies,
financial parameters, existing capacity, retirement/retrofit potential" with
build limits, but its published summary does not surface a *stepped zonal supply
curve / resource-class* construct — the thing GenX-style CEMs use to make VRE
siting economics rise with deployment. This is a place Glassbox goes further on
CEM realism. Conversely, Sienna models **demand-side technologies** and
**retirement/retrofit potential** as investment options; Glassbox handles
retirement *exogenously* (a `retirement_year` / `status` the engines honor), not
yet as an endogenous CEM decision, and has no demand-side investment class.

**3. Reserves / ancillary services.**
Both represent reserves as their own objects rather than generator flags. Sienna
covers **AGC, constant and variable reserves (up/down), and reserve groups**.
Glassbox's `ReserveProduct` carries a `requirement_rule`
(`pct_load` / `pct_vre` / `fixed_mw`) with a `zone_scope`; the CEM/PCM enforce a
spinning-up requirement as a soft (penalized-shortfall) constraint, and the
`pct_vre` term is effectively a *variable* reserve. Glassbox does **not** yet
model AGC, down-reserves, or reserve groups — an honest gap versus Sienna's
services package. (FFR sizing, by contrast, flows in from the dynamics layer.)

**4. Cross-layer requirements are encoded in the schema.**
Distinct to Glassbox: requirements derived in one layer are carried into others
*as data*. `Interface.limit_source` records that a flowgate limit may be a
**stability** limit descending from the dynamics layer, and `SystemConstraint`
(`min_inertia` / `min_synchronous_units` / `rocof_limit` / `min_system_strength`)
carries a dynamics-derived floor **up** into planning and operations, tagged with
`inv`/`ops`/`dyn` facets. Sienna's packages are cleanly separable but its summary
does not describe requirement flow *between* packages; in Glassbox the coupling is
first-class in the schema, which is what lets the tool teach the 6.7 handoffs.

**5. Units formalization.**
Sienna has a dataset-level `UnitSystem` enum (`SYSTEM_BASE / DEVICE_BASE /
NATURAL_UNITS`) that cost/fuel curves reference. Glassbox stores SI per field
with a `base` tag and derives per-unit on demand, plus an explicit machine→system
base conversion in the dynamics engine. Same intent; Sienna's is more formalized
at the dataset level. *Adopting a `UnitSystem`-style enum is a clean future step.*

**6. Persistence, interop & export.**
Sienna: SQLite (3.45+, JSONB) for the static schema + HDF5 for time series, with
a "Case Generator" emitting JSON/HDF5 for downstream tools; language-agnostic via
JSON Schema (Draft-07) + OpenAPI 3.1.0; implementations in Python, Julia, SQL;
explicit interop with GenX/PyPSA/CIM. Glassbox: JSON + npz on the local
filesystem, Pydantic-as-source, Python-only. Two things narrow this gap since the
first cut, though: the facet/unit/base metadata rides in each field's Pydantic
`json_schema_extra`, so `World.model_json_schema()` already emits a **facet-tagged
JSON Schema for free** (verified: every field carries its `facets`), and the
FastAPI surface already serves OpenAPI 3.1 — a language-agnostic export is now
packaging, not new design. Glassbox remains single-user and local by design;
Sienna is built for multi-adopter, GW-scale interchange.

**7. Validation philosophy.**
Both push *domain* validation to the consuming engine rather than the schema.
Sienna says so explicitly ("domain-specific validations left to adopting
libraries"); Glassbox validates structure via Pydantic and validates *phenomena*
via the test/oracle suite. Glassbox now also re-validates structure **live**: the
build-mode editor re-runs full Pydantic validation on every in-place patch
(rejecting bad types/enums/negative values and dangling bus references) and
journals the inverse op, so the "schema validates structure" contract holds for
interactive edits, not just load.

**8. Presentation.**
Glassbox's facets do double duty: besides driving the engines, they drive the
inspector, the operator/overlay UI, and the modeling-layer selector. Sienna is a
pure data schema and (correctly) says nothing about presentation.

## Adding PyPSA — the third model

[PyPSA](https://pypsa.org) is an optimization-first toolkit, and its schema
reflects that: a **network of typed components** held in pandas DataFrames
(`Bus`, `Generator`, `Line`, `Link`, `StorageUnit`, `Store`, `Load`, `Carrier`,
`GlobalConstraint`, …), with time-varying attributes split into a parallel dict
of DataFrames (`<attr>_t`). Where Sienna separates by **package** and Glassbox by
**field-level facet**, PyPSA barely separates at all — one flat component set, and
the "analysis" is a *method* you call (`n.optimize()`, `n.pf()`), not a slice of
the schema.

The sharpest three-way contrast is **investment**, and it is instructive:

| | Mechanism | First-class object? |
|---|---|---|
| **PyPSA** | `p_nom_extendable = True` + `p_nom_min/max` + `capital_cost` **on the asset** | no — the operating component *is* the candidate |
| **Sienna** | a dedicated **Investments** domain package | yes — a separate domain |
| **Glassbox** | `ExpansionCandidate` (nodal) + `ResourcePotential`→`SupplyTranche` (zonal supply curve) | yes — separate entities, two granularities |

Same decision — "what could be built?" — expressed at three depths: a flag, a
package, or a pair of dedicated entities including a stepped supply curve.

Other notable PyPSA positions (verified against the installed component model and
its published schema summary):

- **Transmission expansion** is first-class the same way generation is
  (`Line`/`Link`/`Transformer` `s_nom_extendable`).
- **Unit commitment** is supported (`committable = True`, min up/down, start-up).
- **Reserves / ancillary services** are **not** in the component model — they must
  be added as custom constraints; there is no reserve object.
- **RMS dynamics / EMT**: **absent**. PyPSA covers investment, operations, and
  static AC/DC + linearised power flow, but has no phasor-dynamics or EMT
  representation — its core workflow is the LP, and stability is out of scope.
- **Units** are *documented per attribute but not enforced at runtime*, and there
  is no conversion library — the loosest of the three (Glassbox enforces via
  Pydantic and derives per-unit; Sienna formalizes a `UnitSystem` enum).
- **Interoperability** is strong in practice (a large PyPSA-Eur ecosystem, netCDF
  interchange) but it is a Python library, not a language-neutral schema spec the
  way Sienna publishes JSON Schema + OpenAPI.

The punchline the visual makes obvious: **the physical spine is nearly identical
across all three** — `Bus`, `Generator`, `Load`, and an AC branch line up almost
name-for-name — and the schemas diverge exactly where modeling *ambition*
diverges: investment representation, reserves, and how far up the physics stack
each is built to reach (PyPSA → power flow; Sienna → + stability; Glassbox → the
full teaching stack, each layer pinned to an oracle).

## Adding CIM/CGMES — the fourth model (the exchange standard)

[IEC CIM](https://www.entsoe.eu/data/cim/cim-for-grid-models-exchange/) is not
an analysis schema at all, which is exactly why it belongs in this comparison:
it is the industry's **exchange/asset ontology** — a ~1,500-class UML model
(IEC 61970 transmission / 61968 distribution / 62325 markets) serialized as
RDF/XML, describing what equipment *is* and how it is physically connected,
with no solver semantics whatsoever. **CGMES** (currently 3.0 = IEC
61970-600-1/-2) is ENTSO-E's profile of it, legally required for European TSO
model exchange under EU Regulation 2017/1485.

Three structural ideas separate CIM from everything else on this page:

1. **Node-breaker, not bus-branch.** CIM models every breaker and disconnector
   (`Substation` → `VoltageLevel` → `Bay`; `ConnectivityNode`s;
   `Breaker`/`Disconnector` under `Switch`; the physical bus is *equipment* —
   `BusbarSection`). A power-flow "bus" (`TopologicalNode`) is **computed, not
   stored**: collapse connectivity nodes across closed switches and each
   connected component is one bus. Whether a line is "in service" is a derived
   fact. Every analysis schema here (PyPSA, Sienna, Glassbox pre-rtops) starts
   from the bus-branch view a real EMS *derives* — which is why analysis tools
   import CIM by running topology processing and collapsing it.
2. **The profile split as a data-ownership statement.** CGMES ships a model as
   separate files with different owners and cadences: **EQ** (equipment —
   changes rarely), **SSH** (one scenario's switch states and setpoints —
   hourly), **TP** (derived topology), **SV** (the solved state), plus
   DY/DL/GL. Planning tools live on TP/SV; operations authors EQ/SSH. That
   split *is* the ops/planning schema distinction, standardized.
3. **Identity over convenience.** Every object carries a permanent `mRID`
   across every exchange — the same equipment serves power flow,
   short-circuit, dynamics, asset management, and SCADA binding
   (`Analog`/`Discrete` measurements attach to equipment at `Terminal`s).
   Analysis schemas index by whatever the solver finds convenient.

The four-way contrast in one sentence each:

| Schema | What it fundamentally is | The generator, in its own words |
|---|---|---|
| **CIM/CGMES** | exchange ontology: what exists, analysis-agnostic | `SynchronousMachine` (electrical) + `ThermalGeneratingUnit` (prime mover) — two objects, permanent mRIDs |
| **Sienna** | typed analysis schema, package-partitioned | `ThermalStandard` with closed `PrimeMover`/`ThermalFuel` enums |
| **PyPSA** | problem schema: columns are solver inputs | a `Generator` row: `p_nom`, `marginal_cost`, free-text carrier |
| **Glassbox** | teaching schema, facet-tagged | `Generator` with closed enum + per-field facet/unit metadata |

Notable interop fact: **Sienna has no CIM/CGMES import path** (its parsers are
PSS/E RAW, MATPOWER, CSV) — the mandated European exchange format and the
modern open analysis stacks barely touch. The practical route is
CGMES → PowSyBl or pandapower (`cim2pp`) → MATPOWER → Sienna. For the
hub-and-spoke question this is the sharpest finding yet: *neither candidate
hub speaks the industry's actual exchange standard*, and a CGMES spoke on the
grid-rosetta bench (via `cim2pp`/cimpy, validated against ENTSO-E's MiniGrid
conformity model) would measure exactly what each hub drops of it.

Glassbox's response (per the [Ops Mode PRD](./prd_ops_mode.md), issue #56): an
`rtops` substation layer that mirrors CIM's node-breaker classes by name, a
topology processor so the planning views consume a derived bus-branch model,
and the EQ/SSH/TP/SV split mapped onto world / shift-scenario / derived
topology / results — a legible miniature of the real standard rather than a
caricature.

## What we'd borrow from Sienna

- A dataset-level `UnitSystem` enum to make the SI/device/per-unit choice
  first-class rather than implicit per field.
- Richer services: AGC, down-reserves, and reserve groups, to match Sienna's
  ancillary-services coverage.
- Endogenous **retirement/retrofit** as investment options (Glassbox retires
  exogenously today), and a demand-side technology class.
- Ship the (now nearly-free) language-agnostic export — emit the facet-tagged
  JSON Schema from `model_json_schema()` as a published artifact so the schema
  could interoperate, matching Sienna's interchange goal.
- Optionally, a package/dependency grouping on top of facets for reuse — though
  facets already give most of the modularity benefit for a single tool.

## What's distinctive about Glassbox

- **Field-level facets** as the single source of truth for "which layer sees
  what," driving both engines and UI from one annotation.
- **Cross-layer requirements as data** (`SystemConstraint`, `Interface.limit_source`)
  — the up/down handoffs between stability, operations, and planning are
  encoded in the schema, not just implied by the tools.
- A **stepped zonal supply curve** (`ResourcePotential`/`SupplyTranche`) sitting
  alongside nodal candidates, so VRE siting economics rise with deployment.
- A **transparency contract** (`explain()` on every engine/operator) and an
  **oracle layer** (pandapower/PyPSA/Andes, now including multi-hour dispatch and
  capacity-expansion round-trips) — concerns outside a data schema's scope, but
  central to Glassbox's "inspectable" thesis.
