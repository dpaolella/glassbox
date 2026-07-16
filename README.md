# grid-rosetta

**A hub-and-spoke translation test bench for power-system data schemas.**
Swap PyPSA, Sienna, or Glassbox in and out as the *hub*; run the same
translation task through each; diff what every route drops, approximates,
parks, or invents. Built to turn the "which schema should be the hub?"
debate from a position paper into a measurement.

```
rosetta compare-hubs case14 --from matpower --to glassbox --hubs pypsa,sienna

matpower:case14 -> glassbox, one row per hub

hub         approx  parked  restored  dropped  invented  manual-map  in-sidecar
-------------------------------------------------------------------------------
pypsa            2       0         0        1        30           5           0
sienna           1       0         0        0        13          10           0
```

That table is already a finding: for a **typeless source** (IEEE cases carry
no technology labels), the closed-taxonomy hub charges the mapping debt at
*both* doors (5 generators mapped entering Sienna, 5 again entering
Glassbox's closed enum = 10), while the open hub defers the whole debt to the
final closed door (5). An open hub doesn't erase the cost — it moves it, and
the manifests show exactly where it lands.

## Design commitment: no internal schema

If rosetta had its own neutral intermediate format, that format would just be
a fourth hub contestant and the experiment would collapse. Instead the tool is
**measurement machinery wrapped around pairwise bridges**:

- **Bridge registry** — directed translations between schemas' *native*
  in-memory forms (pandapower nets, PyPSA `Network`s, Glassbox `World`s, a
  Pydantic mirror of the SiennaGridDB subset). A "hub run" composes two
  bridges through the nominated hub.
- **Sidecar protocol** — a standardized envelope for concepts the current
  leg's target cannot hold (namespaced by the schema that understands them,
  e.g. `glassbox:reserve_products`). A later leg whose target understands the
  namespace *restores* them; every other leg carries them through untouched.
- **Coverage manifest** — every bridge must declare what it `translated`,
  `approximated` (and how), `parked`, `restored`, `dropped`, `invented`
  (defaults it made up), and where `manual_mapping_required` — the per-entity
  cost of entering a closed taxonomy. Silent loss is the failure mode this
  tool exists to eliminate.

## What the test suite proves (all executable claims)

| Claim from the hub debate | Test |
|---|---|
| An IEEE case can ride the PyPSA hub into Glassbox and **solve in its engines** (0 unserved) | `test_ieee14_via_pypsa_hub_opens_and_solves_in_glassbox` |
| Reserves survive the **PyPSA hub only via the sidecar** (PyPSA has no reserve object) — parked, then restored | `test_reserves_survive_pypsa_hub_only_via_sidecar` |
| Reserves survive the **Sienna hub by translation** (native `StaticReserve`), with pct-rules flagged as flattened | `test_reserves_survive_sienna_hub_by_translation` |
| A **typeless source** entering a closed taxonomy is a counted per-entity mapping debt | `test_typeless_ieee_case_into_sienna_counts_manual_mappings` |
| The open hub **defers** that debt to the next closed door — it does not erase it | `test_typeless_cost_deferred_by_pypsa_hub_not_erased` |
| An unknown free-text carrier (`unobtainium-chp`) is **counted, never silent** | `test_unknown_carrier_is_counted_not_silent` |

## Usage

```bash
pip install -e ".[all]"          # pypsa + pandapower + glassbox spokes

# translate an IEEE case into a Glassbox world you can open in the app
rosetta translate case14 --from matpower --to glassbox --hub pypsa -o out/
# -> out/world.json + timeseries.npz  (point GLASSBOX_DATA_DIR at it)
#    out/sidecar.json                 (concepts still in transit)
#    out/coverage.json                (the full hop-by-hop ledger)

# same task through each candidate hub, one row per hub
rosetta compare-hubs case14 --from matpower --to glassbox --hubs pypsa,sienna

# what survives a roundtrip through a hub?
rosetta roundtrip path/to/glassbox_world --schema glassbox --hub pypsa --json

rosetta bridges                  # list registered translations
```

## The bench today

Schemas: `matpower` (IEEE cases via pandapower), `pypsa` (native `Network`),
`glassbox` (native `World`), `sienna` (a Pydantic mirror of the SiennaGridDB
subset from the [G-PST data-schema exercise](https://github.com/G-PST/data-schema-exercise),
with the **closed prime-mover/fuel enums** that are the experimental
variable — not affiliated with NREL).

Bridges (directed): `matpower->pypsa`, `matpower->sienna`,
`pypsa<->glassbox`, `pypsa<->sienna`, `glassbox<->sienna`.

Honest limits, on purpose:

- The Sienna mirror is a **subset** (no Investments domain, no dynamics) —
  extendable capacity parked at its door is a real finding about subset hubs,
  not a bug.
- Bridges are pairwise; the bench does not pretend hubs remove the N×M work,
  it *measures how the work distributes*.
- Solve-equivalence checking (translate → solve both ends → compare
  objective *and* decisions, the oracle pattern from Glassbox) is wired for
  the glassbox end only so far; a PyPSA-solve leg is the obvious next step.

## Relationship to Glassbox

[Glassbox](https://github.com/dpaolella/glassbox) plays two roles: a spoke
whose closed, facet-tagged schema makes ingest costs visible, and the
verification engine — a translated world isn't "valid" here until Glassbox's
Pydantic schema accepts it, its persistence round-trips it, and its economic
engine solves it.
