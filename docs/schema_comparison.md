# Power-system data schemas — a comparison, and what makes a good translation hub

A survey of Glassbox's schema against the data models collected in the
[G-PST data-schema-exercise](https://github.com/G-PST/data-schema-exercise)
(assembled for the G-PST Power System Planning Interoperability Data Schema
Workshop), focused on the question: *if you were building a hub-and-spoke
translator that converts model input data between any two frameworks, which
schema approach should the hub use, and why?*

It expands the earlier Sienna-only note ([`sienna_comparison.md`](sienna_comparison.md))
to cover all eight schemas.

## The contenders

| Schema | Origin | One-line identity |
|---|---|---|
| **Glassbox** | this repo | Pedagogical single-`World` schema; field-level facet tags drive both engines and UI |
| **PyPSA** | PyPSA-meets-Earth / TU Berlin | Component-table model in pandas, coupled to its own LP/MILP solver |
| **GenX** | Princeton / MIT | Julia capacity-expansion tool; resource-type structs, CSV inputs |
| **GDM (grid_data_model)** | NREL | Pydantic distribution models (`infrasys`), Pint-typed, CIM-aligned |
| **SAInt** | encoord | Proprietary multi-energy (gas + electric) planning model |
| **Sienna / SiennaGridDB** | NREL | Language-agnostic interchange schema (JSON Schema + OpenAPI), domain packages |
| **CESM** | G-PST / Nodal-Tools (Kiviluoma) | LinkML hub format explicitly built for tool-to-tool interchange |
| **CIM / ENTSO-E** | IEC 61970 + ENTSO-E | International grid-data *exchange* standard (RDF/UML, profiles) |

The single most important distinction is **what each schema is *for*.** Three of
these are tool-internal models that happen to have a documented schema (PyPSA,
GenX, GDM, SAInt), and three were designed from the start as **neutral
interchange formats** (CIM/ENTSO-E, Sienna, CESM). Glassbox is a fourth
category — a teaching schema — but its mechanism is relevant to the hub question.

## At a glance

| Dimension | Glassbox | PyPSA | GenX | GDM | SAInt | Sienna | CESM | CIM/ENTSO-E |
|---|---|---|---|---|---|---|---|---|
| Primary purpose | teach | model+solve | model+solve | model | model | **interchange** | **interchange (hub)** | **exchange standard** |
| Substrate | Pydantic v2 | pandas / CSV→Pydantic | Julia structs / CSV | Pydantic + `infrasys` | proprietary | JSON Schema + OpenAPI | LinkML | RDFS from UML |
| Language-agnostic | no (Python) | no (Python) | no (Julia) | no (Python) | no | **yes** | **yes** | **yes** |
| Separation mechanism | field-level **facets** | none (flat tables) | resource subtypes | component/equipment split | network/scenario/solution | **domain packages** | **mixins** (HasFlow/HasInvestments…) | **profiles** (EQ/TP/SSH/SV/DY…) |
| Units | per-field unit + base | documented, **unenforced** | implicit in field names | **Pint, runtime-enforced** | units library | **`UnitSystem` enum** | **QUDT/UCUM + ISO 4217** | CIM datatypes |
| Existing vs. buildable | **separate entity** (`ExpansionCandidate`) | flag (`p_nom_extendable`) | fields (`Existing_Cap_MW`/`Max_Cap_MW`) | not distinguished | costs only | **separate domain** (Investments) | **mixin** (`HasInvestments`) | n/a (not an optimization schema) |
| Time series | out-of-line npz, by id | out-of-line `*_t` on snapshots | CSV files | `infrasys` arrays | scenario profiles | **HDF5 + DB refs** | **Profile entities + DuckDB** | limited (exchange profiles) |
| Identity / refs | string ids | string columns | symbols | typed refs + validators | graph refs | **UUID supplemental-attr maps** | id refs | **mRID persistent ids** |
| Persistence | JSON + npz | netCDF/HDF5/CSV/Excel | CSV | JSON/SQLite | proprietary | **SQLite (→PG/DuckDB) + HDF5** | **DuckDB hub + SQLite/CSV** | **CIM/XML (RDF)** |
| Validation | Pydantic + oracle suite | runtime checks → Pandera | convention | **multi-layer cross-object** | runtime | JSON Schema + Pydantic + 33 DB triggers | LinkML + generated Pydantic | **SHACL** |

## Per-schema notes

**PyPSA.** One `Network` object, a table per component type (`Bus`, `Generator`,
`Line`, `Link`, `StorageUnit`, `Store`, …), static DataFrame + dynamic `*_t`
DataFrames on a shared `snapshots` index. Existing and buildable capacity live on
the *same* component, distinguished by `p_nom_extendable` plus `p_nom_max` /
`capital_cost`, with the result written back to `p_nom_opt`. Units are documented
but not enforced. Excellent ergonomics for a single co-optimization; weak as a
neutral artifact (inputs and outputs share tables, intent is inferred from flags).

**GenX.** Julia CEM. Resources subtype `AbstractResource` (Thermal, Vre, Hydro,
Storage, MustRun, FlexDemand, VreStorage, Electrolyzer); multiple dispatch
generates technology-specific constraints. Units are baked into field names
(`Inv_Cost_per_MWyr`, `Heat_Rate_MMBTU_per_MWh`); existing vs. candidate via
`Existing_Cap_MW` / `Max_Cap_MW`. CSV inputs + YAML settings. Tightly coupled to
Julia; validation is convention, not a formal schema.

**GDM.** NREL's distribution-system Pydantic models on `infrasys`. Strong on
**runtime units** (Pint enforces dimensionality) and **cross-object validation**
(`@model_validator` checks phase consistency, connectivity, cycles at
construction). Cleanly separates behavioral *components* from physical
*equipment* catalogs. CIM-aligned (IEC 61970) but Python-only, and it does not
model an existing-vs-investment split (it's an asset/topology model, not a
planning one).

**SAInt.** encoord's proprietary multi-energy model (electric **and** gas).
Three layers — Networks (the physical "what"), Scenarios (the operational
"how"), Solutions (results) — so one network is reused across studies. Built-in
units library, inheritance hierarchies for equipment, base vs. derived results.
Proprietary and not forkable; no electric dynamics; investment captured only as
costs.

**Sienna / SiennaGridDB.** NREL's interchange schema. JSON Schema (Draft-07) +
OpenAPI 3.1 are the source of truth; code is generated for Python (Pydantic v2),
Julia, and SQL. **Deliberately flat** — "each component is a self-contained
definition" with no imposed inheritance tree, so adopters map onto their own
abstractions. Modular **domain packages** (Core / Operations / Investments /
Dynamics) allow selective adoption. A dataset-level `UnitSystem` enum
(SYSTEM_BASE / DEVICE_BASE / NATURAL_UNITS) makes units unambiguous; UUID-keyed
**supplemental attributes** carry outage models, groupings, and GeoJSON without
bloating components. Time series in HDF5, metadata in SQLite (DDL auto-generated
from the JSON Schema, 33 triggers enforcing referential integrity), designed to
migrate to PostgreSQL/DuckDB. Explicit interop intent (GenX/PyPSA/CIM).

**CESM (Common Energy System Model).** The schema in this set that is *itself a
hub-and-spoke format.* LinkML-based, MIT-licensed, by Juha Kiviluoma. It uses a
deliberately generic graph abstraction — **Nodes** (balance/storage/commodity),
**Links** (transfer corridors), **Units** (conversion devices), **Ports** — and
shares behavior through **mixins** (`HasFlow`, `HasPenalty`, `HasInvestments`,
`HasProfiles`) rather than per-technology classes. A stated principle is "single
definition for a single thing (e.g. either efficiency *or* heat rate, not both)"
to remove transform-time ambiguity. Units are **QUDT/UCUM**-annotated, currency
is ISO 4217 with a reference year. **DuckDB is the primary interchange hub**, with
Spine/GridDB and CSV+JSON writers as spokes; LinkML emits JSON Schema, Pydantic,
and RDF/OWL. Validated on a ~250-node system round-tripped between CESM and
FlexTool/GridDB.

**CIM / ENTSO-E.** The incumbent international standard for grid-data *exchange*.
RDFS vocabularies derived from UML, organized into **profiles** (EQ Equipment, TP
Topology, SSH Steady-State Hypothesis, SV State Variables, DY Dynamics, DL Diagram
Layout, GL) linked by persistent **mRID** identifiers, validated with **SHACL**,
serialized as CIM/XML (RDF/XML). Purpose-built for boundary matching, model
merging, and version comparison across TSOs. Deliberately *not* a
market-optimization or investment schema — so it covers physical/topology
exchange superbly but says little about capacity-expansion economics.

## Where Glassbox sits

Glassbox is a single-user teaching schema, but its mechanism is the interesting
contribution: a **single `World`** where every field is tagged with the modeling
**facets** that consume it (`core/inv/ops/adq/pf/dyn/emt`). The same `Generator`
reveals heat-rate to `ops`, inertia to `dyn`, capex (now on a separate
`ExpansionCandidate`) to `inv` — an attribute-projection operator slices the one
object per layer, and that same metadata drives the inspector UI. Units carry a
per-field `unit` + per-unit `base`. The recent refactor split existing assets
(`status: ResourceStatus`) from build options (`ExpansionCandidate`), matching
Sienna's Operations/Investments and CESM's `HasInvestments`. It is Python/Pydantic
only and intentionally narrow; the language-agnostic export is deferred.

The thing worth carrying forward from Glassbox is **field-level facet tagging** —
a per-field, machine-readable record of *which downstream analyses consume this
field*. The interchange schemas decompose at the package (Sienna), mixin (CESM),
or profile (CIM) level; Glassbox does it one level finer, at the field.

## Which approach should the translation hub use?

A hub-and-spoke translator writes **N adapters** (each framework ↔ one canonical
hub) instead of **N×(N−1)** pairwise converters. The hub's job is unlike a
solver's: it must be a **neutral, lossless superset** every spoke can map into and
out of without guessing. Judge candidates on:

1. **Neutral / superset semantics** — not biased toward one solver's formulation.
2. **Enforced, machine-readable units** — translation must be *verifiable*;
   "documented but unenforced" units silently corrupt data.
3. **Language-agnostic contract + codegen** — adapters live in Julia (Sienna,
   GenX, PowerSystems), Python (PyPSA, pandapower, GDM), and elsewhere, so the
   contract must be a spec (JSON Schema / LinkML / RDFS), not a class library.
4. **Orthogonal decomposition by concern** — a power-flow-only spoke should map a
   slice and ignore the rest.
5. **Explicit identity, references, extensibility** — stable ids, declared
   foreign keys, and a sanctioned place for framework-specific extras so nothing
   is dropped on round-trip.
6. **A single, unambiguous representation per quantity** — no "either-or" fields
   that force the adapter to guess (CESM's "one definition for one thing").

Scoring the field:

- **Tool-internal models (PyPSA, GenX, GDM, SAInt)** are the wrong layer for the
  hub, however good they are as *spokes*. They are language-locked, units are
  implicit or unenforced (except GDM's Pint), inputs and outputs/flags are
  intermixed (PyPSA, GenX), and their abstractions are shaped by one solver's
  needs. Translate **to and from** them; don't put them in the middle.

- **CIM/ENTSO-E** is the gold standard for *physical-grid exchange* and the right
  hub if the translation problem is topology/power-flow interchange between
  operators. But it is explicitly not a market/investment schema, so it can't be
  the whole hub for planning-tool interchange (capacity expansion, cost curves,
  policy constraints) without significant extension.

- **Sienna and CESM are the two purpose-built candidates, and either is a
  defensible hub.** Both are language-agnostic specs with codegen, formal units,
  out-of-line time series, explicit existing-vs-investment handling, and DB-backed
  interchange.

**Recommendation: model the hub on the CESM/Sienna family — a language-agnostic
spec (LinkML or JSON Schema) with formal units, concern-decomposition, and a
separate investment layer — and pick between them by goal:**

- **CESM** is the most natural hub *as-is*: it was designed for this exact role,
  proven on a real bidirectional round-trip, uses a generic node/link/unit graph
  that is deliberately framework-neutral (so no spoke's vocabulary is privileged),
  QUDT units remove ambiguity, the "single definition per thing" rule directly
  serves translation, and DuckDB gives a working hub store. If the deliverable is
  *a translator*, start here.

- **Sienna** is the better hub if you also want **production-grade rigor and the
  power-systems vocabulary explicit**: JSON Schema + OpenAPI as source of truth,
  auto-generated SQL DDL with referential-integrity triggers, the `UnitSystem`
  enum, UUID supplemental attributes for lossless extras, and a flat,
  no-inheritance design that lowers each spoke's adoption cost. Its stated interop
  targets (GenX/PyPSA/CIM) are exactly the spokes you'd write.

Then **borrow two ideas to strengthen whichever you choose**: Glassbox-style
**field-level facet/provenance tags** (so the adapter mapping layer knows, per
field, which analyses and units a value feeds — finer than package/mixin
grouping), and GDM-style **runtime dimensional unit enforcement** (Pint) so a
mis-mapped unit fails loudly at the boundary instead of silently.

**In one sentence:** make the hub a language-agnostic, unit-formal, concern-
decomposed interchange spec in the CESM/Sienna mold — CESM if you want the ready
hub, Sienna if you want the rigorous power-systems contract — augmented with
Glassbox field-level facets and GDM-style enforced units; treat PyPSA, GenX, GDM,
SAInt (and CIM for the physical layer) as spokes, never as the center.
