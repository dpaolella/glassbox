# Glassbox vs. SiennaGridDB — data-model comparison

A review of [SiennaGridDB's data model](https://github.com/G-PST/data-schema-exercise/blob/main/data_schemas/sienna-griddb_data_model.yaml)
(the NREL Sienna ecosystem schema) against Glassbox's, focused on how each one
organizes one power system for many kinds of analysis.

## TL;DR

Both schemas separate **one physical system** into views for different analyses,
store **time series out-of-line**, attach **explicit per-field units with a base**,
and (now) keep **existing assets distinct from investment options**. The core
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
| Existing vs. buildable | Operations domain vs. **Investments** domain | `Generator`/`Storage` assets vs. **`ExpansionCandidate`** |
| Units | per-field unit metadata + `UnitSystem` enum, base V/power | per-field `unit` + `base` (`system_mva`/`machine_mva`), per-unit derived |
| Time series | HDF5 arrays, referenced from the DB | parquet/npz arrays, referenced by id from `TimeSeriesStore` |
| Substrate | Pydantic (among Julia/SQL) | Pydantic v2 |
| Component shape | "flat and self-contained" | flat entities in one container |
| Dynamics | separate Dynamics package | polymorphic `DynamicModel` + `dyn`/`emt` facets |

The existing-vs-candidate refactor brought Glassbox into line with Sienna's
**Operations vs. Investments** split — candidates are now their own entity with
build limits / resource potential and an operating template, not a boolean on
the asset.

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

**2. Units formalization.**
Sienna has a dataset-level `UnitSystem` enum (`SYSTEM_BASE / DEVICE_BASE /
NATURAL_UNITS`) that cost/fuel curves reference. Glassbox stores SI per field
with a `base` tag and derives per-unit on demand, plus an explicit machine→system
base conversion in the dynamics engine. Same intent; Sienna's is more formalized
at the dataset level. *Adopting a `UnitSystem`-style enum is a clean future step.*

**3. Persistence & interop.**
Sienna: SQLite (3.45+, JSONB) for the static schema + HDF5 for time series, with
a "Case Generator" emitting JSON/HDF5 for downstream tools; language-agnostic via
JSON Schema (Draft-07) + OpenAPI 3.1.0; implementations in Python, Julia, SQL;
explicit interop with GenX/PyPSA/CIM. Glassbox: JSON + npz on the local
filesystem, Pydantic-as-source, Python-only (the PRD deferred a JSON Schema /
LinkML exporter). Glassbox is single-user and local by design; Sienna is built
for multi-adopter, GW-scale interchange.

**4. Validation philosophy.**
Both push *domain* validation to the consuming engine rather than the schema.
Sienna says so explicitly ("domain-specific validations left to adopting
libraries"); Glassbox validates structure via Pydantic and validates *phenomena*
via the test/oracle suite.

**5. Presentation.**
Glassbox's facets do double duty: besides driving the engines, they drive the
inspector, the operator/overlay UI, and the modeling-layer selector. Sienna is a
pure data schema and (correctly) says nothing about presentation.

## What we'd borrow from Sienna

- A dataset-level `UnitSystem` enum to make the SI/device/per-unit choice
  first-class rather than implicit per field.
- A language-agnostic export (JSON Schema / OpenAPI) so the facet-tagged schema
  could interoperate, matching Sienna's interchange goal (the PRD already lists
  this as an optional exporter).
- Optionally, a package/dependency grouping on top of facets for reuse — though
  facets already give most of the modularity benefit for a single tool.

## What's distinctive about Glassbox

- **Field-level facets** as the single source of truth for "which layer sees
  what," driving both engines and UI from one annotation.
- A **transparency contract** (`explain()` on every engine/operator) and an
  **oracle layer** (pandapower/PyPSA/Andes) — concerns outside a data schema's
  scope, but central to Glassbox's "inspectable" thesis.
