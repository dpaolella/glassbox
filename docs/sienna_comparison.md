# A reference model for grid data schemas — and how the real ones measure up

There is no shortage of power-system data schemas: optimization toolkits (PyPSA),
production analysis stacks (NREL Sienna), the industry's exchange ontology
(IEC CIM / ENTSO-E CGMES), capacity-expansion formats (OSeMOSYS), enterprise
market models (Plexos), open production hubs (VeraGrid / MultiCircuit), the old
test-case lingua franca (MATPOWER/IEEE), and this project's own teaching schema
(Glassbox). They overlap in the middle and diverge at the edges, and it is hard
to say anything crisp about "which is best" because they are built for different
jobs.

This document takes a different tack. Instead of ranking schemas against each
other, it first asks: **what would an ideal shared grid schema represent, and how
would it be designed?** — and then measures every real schema against *that*.
The reference model is not a proposal to build; it is a **ruler**.

> **Companion artifacts.** The interactive visual is
> [`docs/schema_atlas.html`](./schema_atlas.html) (self-contained; open in a
> browser) — it shows the reference and the four most robust schemas
> (Glassbox / Sienna / PyPSA / CGMES) as a capability matrix, an entity "Rosetta
> stone," and a reach-up-the-physics-stack ladder. The
> [`grid-rosetta`](https://github.com/dpaolella/grid-rosetta) bench is the
> *empirical* companion: it translates real models pairwise between these schemas
> and records, per property, what each route drops — running evidence for the
> gaps this document names.

> **Grounding.** Claims below are checked against each schema's published data
> model (Sienna's [SiennaGridDB](https://github.com/G-PST/data-schema-exercise),
> PyPSA's component model, the CGMES profiles, VeraGridEngine's device registry),
> Glassbox's live Pydantic introspection, and the grid-rosetta coverage manifests.
> Coverage reflects the *data model*, not the surrounding tooling.

## The idea: a yardstick, not a hub

grid-rosetta makes a deliberate design commitment: it has **no internal schema**.
A neutral intermediate format would just be a fourth hub contestant, and routing
every translation through it would bias the very loss it is trying to measure. So
why introduce a reference model here at all?

Because a reference model is **not a hub**. The distinction is the whole point:

- A **hub** is a *waypoint* — data flows *through* it, so its blind spots corrupt
  what passes across.
- A **reference model** is a *measuring stick* — no data ever touches it. It only
  enumerates *what a complete grid schema could represent*, so each real schema's
  reach can be read off against a fixed scale.

The two are complementary rulers. grid-rosetta's coverage manifests measure
**translation loss** — pairwise, empirical, "what did this route actually drop?"
The reference model measures **representational reach** — "what can this schema
express at all, versus the full set of things worth expressing?" One is a
dynamic measurement between two schemas; the other is a static scorecard for one.

Two rules keep the reference honest and stop it from being "Glassbox with extras":

1. **It is a union, not an invention.** Its capabilities are the union of what the
   surveyed schemas already represent, organized on a schema-neutral spine (the
   analysis ladder). Nobody's structure wins; the reference is assembled from the
   field.
2. **It must expose every schema's gaps — including Glassbox's.** A ruler that
   only measured others would be a trophy. The sections below are explicit about
   where Glassbox falls short of its own ideal.

## What a shared grid schema should represent

### Design principles

Twelve principles, each attributed to the schema that makes the case for it best.
An ideal schema would hold all twelve at once; no real one does.

1. **One stored world, many views.** Store the finest representation once (nodal,
   node-breaker, full multi-year chronology) and *derive* every coarser view by
   projection, rather than storing "the zonal data" and "the nodal data"
   separately. — *Glassbox's central thesis; CGMES's computed bus is the same move.*
2. **Node-breaker is the base; the bus is computed.** Model the physical
   switchyard — busbars, connectivity nodes, breakers and disconnectors — and let
   the power-flow "bus" (a `TopologicalNode`) be a *computed* connected component
   across closed switches, never stored. Whether a line is in service is then a
   derived fact. — *CIM/CGMES.*
3. **Structure and operating state are separate.** The equipment model (what
   exists, as-drawn) is a different thing from a scenario's switch positions and
   setpoints, and they change on different cadences. — *CIM's EQ vs SSH profiles;
   Glassbox's `Switch.normal_open` vs `Switch.open`.*
4. **Identity is permanent across exchanges.** Every object carries a stable id
   that survives every translation, so the same physical asset is recognizable
   across power flow, dynamics, markets, adequacy, and SCADA. — *CIM's `mRID`.*
5. **Existing assets and buildable options are different objects.** An asset has a
   lifecycle/status; a candidate is a separate entity with siting, build limits,
   financials, and an operating template — never a boolean on the asset. — *Sienna's
   Operations vs Investments domains; Glassbox's `ExpansionCandidate`.*
6. **Technology is an open label with an optional closed classification.** Carry
   the source's native free-text type *and* allow a mapping to a closed enum, so
   the cost of entering a taxonomy is **explicit and deferrable**, not silently
   forced at the door. — *The grid-rosetta bench's typeless-source finding.*
7. **Units and base are per-field metadata.** Every numeric field declares its SI
   unit and its per-unit base; conversion is derivable, not implicit in code. —
   *All three formal schemas (Sienna's `UnitSystem`, Glassbox's `unit`+`base`,
   PyPSA's per-attribute units).*
8. **Time series live out-of-line.** Arrays are referenced by id, never inlined
   into the component records. — *Universal (HDF5, npz, `_t` DataFrames).*
9. **Cross-layer requirements are carried as data.** A stability-derived flow
   limit, a minimum-inertia floor, an FFR reserve requirement is a *field* that
   flows between layers — not a comment, not tribal knowledge. — *Glassbox's
   `SystemConstraint` and `Interface.limit_source`.*
10. **Per-field capability tags.** Each field is tagged with the analyses that
    consume it, so any tool can slice the one world down to exactly what it needs.
    — *Glassbox's facets; Sienna's packages are the coarse-grained version.*
11. **Ingest is auditable.** When data crosses in from another schema, what was
    approximated, dropped, or defaulted is recorded per property — never lost
    silently. — *VeraGrid's `DataLogger`; grid-rosetta's coverage manifest.*
12. **The serialization is language-neutral and spec-published.** A schema anyone
    can implement against, in a portable wire format with a published contract. —
    *Sienna's JSON Schema + OpenAPI; CIM's RDF/XML.*

### The capability taxonomy

*What* an ideal schema must be able to represent, organized on the analysis
ladder (coarsest economics to finest physics) plus a cross-cutting metadata band.
This list is the row set the schemas are scored against.

- **Topology & network (physical).** Node-breaker substations (busbars,
  connectivity nodes, breakers/disconnectors, terminals); base voltages; AC
  branches with sequence impedances; DC/HVDC; shunts and reactive devices; the
  derivable bus-branch view.
- **Assets.** Generators (native label *and* prime-mover/fuel); storage with
  independent power and energy; loads; hydro with reservoirs/cascades; converters
  (grid-following / grid-forming); an asset lifecycle/status.
- **Investment (capacity expansion).** Nodal candidates *and* zonal supply curves
  (rising \$/MW with deployment); build limits and financials; transmission
  expansion; endogenous retirement/retrofit; demand-side options.
- **Operations & markets.** Unit-commitment parameters (min up/down, start cost);
  reserves in both directions (spinning / non-spinning / regulation / FFR), AGC,
  and reserve groups; a balancing area and its ACE context (frequency bias, tie
  capacity, scheduled interchange); interfaces/flowgates; locational prices from
  duals; scarcity (ORDC); bids and offers.
- **Security (steady-state).** Operational limits (thermal and stability, normal
  and emergency); contingency lists; PTDF / monitored elements; voltage schedules
  and tap changers.
- **Adequacy.** Forced-outage models (MTTF/MTTR); correlated multi-year weather
  draws; value of lost load.
- **Dynamics (RMS).** Machine models, inertia, governor, AVR/exciter; converter
  control (GFL/GFM); FFR; and the min-inertia / RoCoF requirements that flow up
  from here.
- **EMT.** Sequence and harmonic impedances; filter (LCL) parameters; converter
  control-loop parameters.
- **Measurement & telemetry.** Measurement classes (`Analog`/`Discrete`) bound to
  terminals, so a state estimator has something to run on. — *CIM's territory.*
- **Metadata & representation (cross-cutting).** Permanent identity; units + base;
  per-field capability tags; the structure-vs-state split; a provenance/loss
  ledger; a language-neutral published spec.

## How the schemas measure up

The four most robust schemas — Glassbox, Sienna, PyPSA, and CGMES — scored against
the capability taxonomy. `●` first-class in the data model, `◐` partial / derived
/ via a flag, `○` not in the data model. (The specialist formats — OSeMOSYS,
Plexos, VeraGrid, MATPOWER/IEEE — are covered in their own section below; they are
deliberately partial.)

| Capability | Glassbox | Sienna | PyPSA | CGMES |
|---|:---:|:---:|:---:|:---:|
| Node-breaker topology (bus computed) | ◐ `rtops` layer | ○ bus-branch | ○ bus-branch | ● native |
| Structure vs. operating state (EQ/SSH) | ● `normal_open`/`open` | ○ | ○ | ● native |
| Permanent identity across exchanges | ◐ ids, not exchange-grade | ◐ | ◐ | ● `mRID` |
| Existing vs. buildable, kept apart | ● `ExpansionCandidate` | ● Investments domain | ◐ `extendable` flag | ○ not an analysis concern |
| Buildable as a stepped supply curve | ● `ResourcePotential` | ◐ technologies + limits | ○ one `p_nom_max` | ○ |
| Open label + closed classification | ◐ closed enum only | ○ closed pair only | ● free-text carrier | ● two objects (machine + unit) |
| Units + base per field | ● | ● + `UnitSystem` | ● documented | ◐ profile-declared |
| Time series out-of-line | ● npz | ● HDF5 | ● `_t` frames | ◐ separate profiles |
| Cross-layer requirements as data | ● `SystemConstraint` | ○ packages separable | ○ | ○ |
| Per-field capability tags | ● facets | ◐ packages (coarse) | ○ | ○ |
| Reserves / ancillary services | ◐ spinning-up rule | ● AGC + up/down + groups | ○ custom constraint | ○ market profile (62325) |
| Balancing area / ACE | ● `OperatingArea` | ○ string area tag | ○ | ◐ `ControlArea` |
| Unit commitment | ● MILP UC | ● Operations domain | ● `committable` | ○ |
| Steady-state security · N-1 | ● NR + contingencies | ● network domain | ● AC + linear | ◐ topology, no solver |
| RMS dynamics | ● `dyn` facet | ● Dynamics domain | ○ | ◐ DY profile params |
| EMT / resonance | ◐ `emt` micro-examples | ○ | ○ | ○ |
| Resource adequacy (Monte Carlo) | ● `adq` facet | ◐ ecosystem tooling | ○ | ○ |
| Measurement / telemetry binding | ◐ runtime kernel | ○ | ○ | ● `Analog`/`Discrete` |
| Auditable ingest provenance | ◐ via grid-rosetta | ○ | ○ | ◐ DataLogger-style tools |
| Language-neutral published spec | ◐ derivable | ● JSON Schema + OpenAPI | ◐ Python + netCDF | ● RDF/XML |

Read the columns and a character emerges; the rest of this section walks the
sharpest contrasts, capability by capability, since that is where the design
choices actually live.

**Investment — the clearest fork.** The same decision ("what could be built?") is
expressed at four depths. **PyPSA** overloads the operating asset with a
`p_nom_extendable` flag plus `p_nom_min/max` and `capital_cost` — the candidate
*is* the component. **Sienna** gives investment its own **Investments** domain
package (supply/storage/network options, financials, retirement/retrofit
potential, and demand-side technologies). **Glassbox** gives it two dedicated
representations — `ExpansionCandidate` (nodal: "should we build *this* here?")
*and* `ResourcePotential` → `SupplyTranche` (a zonal stepped supply curve, so the
best sites exhaust first and a technology's cost *rises* with deployment, the
GenX-style construct). **CGMES** has none of this — it is an exchange ontology,
not an analysis schema, and "what could be built" is not a question it asks.
Against the reference, Sienna is most complete (it models demand-side and
endogenous retirement, which Glassbox does *not* — Glassbox retires exogenously
via `retirement_year`/`status`), while Glassbox alone carries the stepped supply
curve.

**Operations — where reserves are the tell.** grid-rosetta measured this directly
(`tests/test_ops_interop.py`): round-trip an operations-bearing world through the
PyPSA and Sienna hubs and read what each carries. **Reserves are the only
operations concept a planning schema carries natively — and only Sienna** (it
translates them to `StaticReserve`; PyPSA can hold them only in grid-rosetta's
sidecar, and CGMES relegates them to the market profile, IEC 62325). Sienna is
strongest here overall: AGC, up- *and* down-reserves, reserve groups. Glassbox's
`ReserveProduct` carries a `requirement_rule` (`pct_load`/`pct_vre`/`fixed_mw`,
the `pct_vre` term a variable reserve) but has no AGC, down-reserves, or groups —
a real gap versus the reference. The deeper control-room layer — the node-breaker
substation model and the balancing area / ACE context — is where the split is
starkest: it has *no home in either planning hub* and survives a round-trip only
as byte-identical sidecar baggage, never as a native object. Only Glassbox (via
`rtops`) and CGMES model it at all.

**Node-breaker topology & the structure/state split — CGMES's home turf.** CGMES
is the reference standard for principles 2 and 3, and it is why they are in the
reference at all. It authors the switchyard (`Substation` → `VoltageLevel` →
`Bay`; `ConnectivityNode`s; `BusbarSection` as *equipment*; `Breaker`/`Disconnector`
under `Switch`) and *computes* the `TopologicalNode`. It splits the model across
profiles — **EQ** (equipment, changes rarely), **SSH** (one scenario's switch
states and setpoints), **TP** (derived topology), **SV** (the solved state) — so
the structure/state distinction is a data-ownership fact, not a convention.
PyPSA, Sienna, and pre-`rtops` Glassbox all start from the bus-branch view a real
EMS *derives*. Glassbox's response is the `rtops` layer: a legible miniature of
CGMES's node-breaker classes, with a topology processor so the planning engines
still consume a bus-branch world — and grid-rosetta's `cgmes -> glassbox` bridge
is, precisely because of this correspondence, a **~1:1 CIM-class rename** rather
than a translation. Against the reference, only CGMES has this natively in the
*core*; Glassbox has it as an additive layer, not in its planning spine.

**Cross-layer requirements — Glassbox's home turf.** Distinct to Glassbox:
requirements derived in one layer are carried into others *as data*.
`Interface.limit_source` records that a flowgate limit is a **stability** limit
descending from the dynamics layer; `SystemConstraint` (min-inertia,
min-synchronous-units, RoCoF, min-system-strength) carries a dynamics-derived
floor **up** into planning and operations, tagged with `inv`/`ops`/`dyn` facets.
None of the others encode requirement *flow* between layers — Sienna's packages
are cleanly separable but silent on the coupling, which is exactly what lets
Glassbox teach the stability→operations handoffs.

**Identity & interchange — the exchange schemas win.** CGMES's `mRID` and
Sienna's published JSON Schema + OpenAPI (implementations in Python/Julia/SQL) are
the reference for principles 4 and 12. Glassbox is Python-first and local, though
the gap is narrower than it looks: its facet/unit/base metadata rides in each
field's Pydantic `json_schema_extra`, so `World.model_json_schema()` already emits
a **facet-tagged JSON Schema for free**, and the FastAPI surface already serves
OpenAPI 3.1 — a language-neutral export is packaging, not new design. A notable
interop fact sharpens the whole picture: **Sienna has no CIM/CGMES import path**
(its parsers are PSS/E, MATPOWER, CSV). The mandated European exchange format and
the modern open analysis stacks barely touch — which is itself the strongest
argument for measuring both against one reference rather than against each other.

**Provenance — the newest principle, and the least served.** Principle 11
(auditable ingest) is the one almost nobody satisfies in the schema itself.
VeraGrid threads a `DataLogger` through every parser; grid-rosetta makes the loss
ledger a first-class output of every translation. But none of the four core
schemas *store* provenance as part of the model — it lives in the tooling. This is
a genuine frontier the reference names and the bench operationalizes.

## Where each schema meets and misses the ideal

- **CGMES/CIM** — *the exchange ontology.* Meets the reference on the physical and
  identity principles better than anything else: node-breaker, EQ/SSH,
  permanent `mRID`, terminal-bound measurements, a ~1,500-class model serialized
  as RDF. Misses everything above the physics: no investment, no reserves in the
  core, no solver semantics at all. It describes what *is*, exhaustively, and
  leaves what to *do* with it to the analysis schemas.
- **Sienna** — *the production analysis stack.* The most complete on the
  operations and investment principles (AGC, up/down reserves, reserve groups; a
  full Investments domain with demand-side and retirement; a `UnitSystem` enum;
  JSON Schema + OpenAPI + multi-language). Misses the node-breaker base (bus-branch
  only), the structure/state split, cross-layer requirement flow, and — notably —
  any CIM import path.
- **PyPSA** — *the optimization problem schema.* Its columns are solver inputs;
  investment and unit commitment are flags on the component, and the AC/linear
  power flow is built in. Misses reserves (custom constraints), all dynamics and
  EMT, node-breaker topology, and enforced units — deliberately, because its
  center of gravity is the LP, not the data model.
- **Glassbox** — *the teaching schema.* Meets the reference on "one world many
  views" (facets), the stepped supply curve, cross-layer requirements, the widest
  physics reach (adequacy and EMT included, each pinned to an oracle), and — via
  `rtops` — the node-breaker/operations layer. **Where it misses its own ideal:**
  no AGC / down-reserves / reserve groups; retirement is exogenous and there is no
  demand-side investment class; node-breaker lives only in `rtops`, not the
  planning spine; telemetry/state-estimation is a runtime kernel, not schema-bound
  measurements (no per-terminal `Analog`/`Discrete`); it is single-user and local
  rather than a language-neutral multi-adopter spec; and it has no market/bid
  objects. Naming these is the point of having a ruler.

## Specialist formats on the bench

Four more schemas are on the grid-rosetta bench precisely because they are
*partial* — each is illuminating at one edge of the reference and blank at others.

- **OSeMOSYS** (otoole-style CSV) — the **capacity-expansion extreme**: open
  free-text technology names (like PyPSA) *plus* a native investment side (which
  the Sienna mirror lacks), but **no network** (single copper-plate region) and
  **no chronology** (diurnal timeslices). Measured as a hub, an OSeMOSYS payload
  scores CEM-complete and power-flow-impossible — which is exactly what it is.
- **Plexos** (MasterDataSet XML) — the **typeless-identity extreme**: enterprise
  market models whose objects may carry **no category at all**, so "what is this
  unit?" has no answer in the source. It is the sharpest test of principle 6 (the
  mapping debt is unavoidable and must be counted, not hidden).
- **VeraGrid / MultiCircuit** — the **mature-production extreme**: a decade-old
  hub schema with 153 CGMES device classes, parsers for the formats industry
  actually uses (PSS/E, CGMES, UCTE, PowerFactory), and a `DataLogger` that is the
  clearest existing implementation of principle 11. Its breadth-vs-subset
  asymmetry against a teaching schema is itself a finding.
- **MATPOWER / IEEE cases** — the **minimal test-case lingua franca**: a bus/branch
  matrix with no technology labels at all. Useful precisely because it is the
  floor — the smallest thing that is still a grid model, and a clean typeless
  source for the mapping-debt experiments.

The pattern across all four: the reference model tells you *where* each is blank,
and grid-rosetta measures *what it costs* to route real data through them anyway.
