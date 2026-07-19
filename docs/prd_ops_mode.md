# PRD: Ops Mode — a control-room simulator on the planning-fixed system

**One sentence:** simulate one operating day (a 12-hour shift, 05:30→17:30) on the exact system the planning views built, with the user in the operator's seat — graded the way NERC grades real control desks.

This PRD is grounded in three research passes (sources at bottom): real control-room doctrine (NERC BAL/TOP/IRO/EOP standards, EMS function stacks, dispatcher-training-simulator design), Grid2Op's formalization of grid operation as a sequential decision environment (the L2RPN rulebook), and NREL Sienna's PowerSimulations staged-simulation architecture. Where this PRD names a number or a rule, it is a real one, not an invention.

---

## 1. Purposes → features (the contract)

| Purpose | How this PRD delivers it |
|---|---|
| **Operator vs. planner mindset** | Same world, two lenses: the planner chose capacity over years with perfect hindsight; the operator gets *this* day, *this* forecast error, *this* forced outage, a 15-minute DCS clock, and no ability to build anything. The shift report card makes the difference quantitative (§8). |
| **Ops vs. planning schema requirements** | Every new entity is facet-tagged `rtops` and lands in a schema-delta table (§9). The headline lesson: planning schemas represent *one trusted truth*; ops schemas represent *degrees of trust in competing versions of the truth* (telemetry vs. state estimate vs. forecast vs. actual). |
| **Teach how a control room works** | The UI *is* an EMS: frequency/ACE strip, RTCA panel, alarm queue, reserves ladder, interchange, one-line with rho coloring (§7). Every alarm links to a plain-language "why" in the existing EngineMath/transparency style. Guided first-shift tour + graded challenge scenarios (§10). |
| **Ops/planning data interoperability** | Ops-sim artifacts get real schema representations so the grid-rosetta bench can measure whether planning schemas (PyPSA/Sienna) can carry them — reserves already demonstrated the pattern; telemetry/dispatch-instruction/clearance concepts extend it (§9.3). |
| **How planning models could evolve** | The sim logs every moment where operational reality bites a planning assumption (ramp scarcity during the duck-curve neck, reserve deliverability behind a constraint, N-1 redispatch cost). The end-of-shift report includes a "what planning missed" section computed from these events (§8.3). |

**Realism budget:** the toy network keeps everything tractable — full 8760-hour realism is not the goal; *doctrinal* realism is. We use real cadences (5-min SCED, 4-s AGC ticks abstracted to 1 per sim-second, 30-min assessment clocks), real metric definitions (ACE, CPS1, BAAL), and real procedure names (RUC, DCS, EEA, clearances). We deliberately skip what the research says to skip (§12).

---

## 2. The frame: one day, one desk, the planned system

- **System under operation:** the current world, optionally the *committed* world from `plan_then_operate` (`/api/scenario/plan_then_operate` already materializes CEM builds via `world_with_builds`). Planning fixes it; ops cannot change capacity — only commit/dispatch/switch what exists.
- **The player's role:** a combined **BA + TOP** desk (the common real-world configuration): balance the area (ACE, reserves, interchange) *and* operate the network within limits (flows, switching, clearances). The **RC** is simulated — it declares EEA levels and issues directives via the event log.
- **The external world:** the toy system becomes an *area within a larger interconnection*: one or two **tie lines** connect to an external area with large inertia, a frequency bias, and an hourly **scheduled interchange**. This is what makes ACE real (you can lean on the ties, and the sim can tell), and it is the single most important addition for balancing realism.
- **The clock:** sim runs on a controllable clock (freeze / 1× / 10× / 60×), 5-minute market steps, 1-second UI ticks. A full shift at 60× is ~12 wall-minutes; challenges use shorter windows.
- **Determinism:** every shift is a seeded `ShiftScenario` — same seed, same day, byte-identical replay (the oracle/testing story depends on this, §11).

## 3. The simulation kernel (`glassbox/engines/rtops.py`)

Staged architecture borrowed from Sienna PowerSimulations (decision models + feedforwards + an emulation layer), with Grid2Op's protection rules as the physics of consequence:

### 3.1 Stage 0 — Day-ahead (runs once at shift start, produces the turnover briefing)
- Hourly **unit commitment** over the operating day using the **forecast** series — this is the existing PCM/`economic_core` UC path, unchanged.
- Output: commitment schedule, DA basepoints, scheduled interchange per hour, planned maintenance clearances active today.
- Presented as the **turnover briefing** (the 05:30 handover): "units committed, forecast peak 3,410 MW at HE17, line L7 out for maintenance 09:00–15:00, storm watch this afternoon."

### 3.2 Stage 1 — Real-time market (every 5 sim-minutes)
- **RT-SCED**: re-solve dispatch for a 1-hour lookahead at 5-min resolution using *actuals-so-far + short-term forecast*, with the DA commitment **fixed as a feedforward** (Sienna's `SemiContinuousFeedforward` pattern — on/off status honored, basepoints re-optimized within ramp limits). Reuses `build_dispatch_model` with commitment bounds pinned.
- Outputs **basepoints and LMPs** each interval. Nodal prices feed the UI; congestion appears as price separation exactly as in the planning views (concept continuity).
- **HRUC** (hourly): if the RT stage projects a shortfall, the sim proposes quick-start commitments the operator approves/denies — teaching that commitment is a *decision*, not an outcome.

### 3.3 Stage 2 — Actuals & AGC emulation (every sim-second ≈ one AGC cycle)
- **Actuals process** (the gridfm-datakit recipe): per-bus actual load/VRE = forecast × global error factor × per-bus correlated noise, evolving as a bounded random walk. Forecast error is the antagonist of the morning ramp and the duck-curve neck.
- **ACE**, computed with the NERC Reporting ACE definition and sign conventions:
  `ACE = (NIa − NIs) − 10B(Fa − Fs)` (MW; negative = under-generating, leaning on the ties).
- **Frequency:** quasi-steady deviation from area imbalance and combined bias (`Fa − Fs = imbalance / (10·(B_area + B_ext))`); on discrete events (unit/line trips) the existing **SFR/dynamics engine** (`assemble_frequency_system`) is invoked to compute and display the transient — RoCoF, nadir, settling — so students *see* inertia matter without the sim integrating swing equations continuously.
- **AGC:** distributes regulation among reserve-eligible units toward ACE zero-crossing, within ramp rates. Regulation exhaustion → sustained ACE → BAAL clock starts (§8.1).

### 3.4 Stage 3 — Network & protection (every 5-min step, and immediately on events)
- DC power flow on actuals (existing machinery); **rho = |flow| / rating** is the hero metric, per Grid2Op.
- **Protection emulation, three numbers (Grid2Op parameter names in parens):**
  - rho > 1.0 vs `rating_normal_mva` → **warning** (alarm, amber);
  - rho > 1.0 vs `rating_emergency_mva` sustained for **2 consecutive steps** (`NB_TIMESTEP_OVERFLOW_ALLOWED`) → protection **trips the line** (soft overflow);
  - rho > **1.5× emergency** (`HARD_OVERFLOW_THRESHOLD` analog) → instant trip;
  - tripped lines are unavailable for **6 steps / 30 min** (`NB_TIMESTEP_RECONNECTION`).
  - Trip → re-solve → next-worst line may now overload → the loop **is** the cascade simulator. Glassbox's three-tier line ratings (normal/emergency/lt), unused by planning engines, finally earn their keep — itself a schema lesson.
- **Forced outages:** drawn from per-unit `mttf_h`/`mttr_h` via the adequacy engine's existing outage machinery, seeded per scenario. Weather events (from `detect_events`) modulate outage rates and VRE/load actuals on storm-day scenarios.
- **RTCA:** after each network solve, screen the N-1 list (all lines + largest units; the toy network makes exhaustive screening trivial) and rank post-contingency violations — "if L4 trips, L7 reaches 112% of emergency." Feeds the RTCA panel and starts **30-minute SOL clocks** (TOP-001 Real-time Assessment cadence) when a post-contingency violation appears.
- **Failure state:** islanded load or diverged solve = **blackout event** (Grid2Op's game-over), but educational framing: the shift continues into a scored restoration epilogue (reconnect lines, pick up load in blocks) rather than a hard game-over screen.

## 4. Operator actions

Grid2Op's legality model, adopted wholesale: **ambiguous** actions (nonsense: redispatching a wind unit above availability) are rejected with an explanation; **illegal-now** actions (line under cooldown, unit within min-down-time) become no-ops with a visible reason — the explanation *is* the teaching affordance.

| Action | Constraint that makes it interesting |
|---|---|
| **Redispatch** (offset a unit's basepoint) | ramp rates; SCED re-optimizes around your offset; costs are scored |
| **Commit / decommit** quick-start units | start cost, min up/down times, notification lag |
| **Deploy reserves** (spin → non-spin) | consumes the product; DCS requires *restoring* it within 90 min |
| **Curtail VRE** | scored (both cost and renewables-use metric, à la L2RPN 2023) |
| **Switch a line out / in** | cooldowns (`NB_TIMESTEP_COOLDOWN_LINE` = 3 steps), one switching action per step (`MAX_LINE_STATUS_CHANGED` = 1) — "you can't fix everything at once" |
| **Adjust interchange** with the external area | takes effect at the next schedule step (hourly, ramped :50→:10); emergency schedule changes need RC approval (simulated) |
| **Grant / recall maintenance clearances** | recall takes 30 sim-min (crews drive back); denying the morning clearance has consequences at 14:00 |
| **Manual load shed** (by block) | EEA-3 doctrine: last resort, but scored *better* than letting protection do it for you |
| **Acknowledge alarms** | unacknowledged critical alarms degrade the situational-awareness score |
| **Study mode** (`obs.simulate` analog) | test any action against the short-term forecast before committing; optionally rationed per step — Grid2Op's single best pedagogical feature |

## 5. What the sim never lets you forget (the doctrine details that make it real)

- **Interchange steps at the top of each hour** — flows jump at :00, ramped :50→:10; the operator learns to anticipate rather than react.
- **The morning ramp and the duck-curve neck** are the two canonical hard periods; the default scenario library centers on them (§10).
- **Three-part communication flavor** in the event log: directives appear as issued/repeated/confirmed entries. Cosmetic, cheap, and it teaches the culture.
- **The RC exists above you**: EEA declarations, IROL directives, and "operate conservatively" instructions arrive as events you must comply with (TOP-001: comply or explain).
- **SE health is itself a signal** (Phase 3): when telemetry degrades, the state-estimate confidence banner degrades with it — operating blind is an emergency in its own right (the 2003 blackout's dead alarm processor).

## 6. API surface

Server-holds-session (survives tab switches — avoids the #50 class of bug by design):

```
POST /api/opsim/start        {scenario_id | seed, world: current|committed, speed}
GET  /api/opsim/state        full dashboard state (1 Hz poll; includes sim clock)
POST /api/opsim/action       {type, target, params} → applied | rejected {reason}
POST /api/opsim/clock        {freeze | speed}
POST /api/opsim/study        {action} → simulated next-interval outcome (never mutates)
GET  /api/opsim/report       end-of-shift report card (also mid-shift, partial)
GET  /api/opsim/log          operator action log + event log (exportable, replayable)
POST /api/opsim/instructor   {inject_event} — instructor console (§10)
```

All numeric outputs carry units per the existing units contract; every event carries a `why` explanation keyed to the transparency-contract style.

## 7. UI: the Control Room tab

A new top-level tab ("Control Room"), one screen, everything visible at once (the research's 10-item dashboard, adapted):

1. **Frequency strip** — Fa vs 60.000, continuously animated (the IMAGINARY lesson: frequency must *feel* alive); SFR transient overlay on trips.
2. **ACE trace** — instantaneous + clock-minute average, BAAL band shaded, violation clock when outside.
3. **Load** — actual vs forecast with error shading; ramp-headroom indicator.
4. **Reserves ladder** — regulation / spin / non-spin vs requirements; deployment state.
5. **Interchange** — per-tie actual vs schedule; next hour's step preview.
6. **RTCA panel** — top post-contingency violations, % of limit, SOL clocks counting down.
7. **One-line / map** — the existing `NetworkCanvas` with rho coloring (green→amber→red→tripped), breaker states, clearance tags. The playback strip generalizes to live mode (subsumes #52's load/price tracks).
8. **Alarm queue** — priority-sorted, unacknowledged count, flood-mode compression during cascades.
9. **LMP sparkline + basepoint table** — the market view (sortable, per #47's table machinery).
10. **Event log + action bar** — the operator's tools (§4), study-mode button, clock controls.

**Instructor console** (collapsible, DTS-style): inject events (unit trip, line fault, RTU failure, storm acceleration), freeze/snapshot/backtrack, replay a completed shift for debrief.

## 8. Scoring: graded like a real desk

### 8.1 Reliability & control (NERC-derived)
- **CPS1-style score**: did your ACE oppose or aggravate frequency error (interval-average correlation, scaled so ≥100% = compliant)?
- **BAAL violations**: minutes outside the ACE band; any single excursion > 30 consecutive min is a major deduction.
- **DCS events**: after each Reportable contingency (unit trip ≥ threshold), ACE recovery within **15 min** (pass/fail) and reserve restoration within **90 min**.
- **SOL/IROL clocks**: post-contingency violations cleared within **30 min**; an uncleared IROL-class violation is the single largest deduction short of blackout.
- **Unserved energy**, valued at VOLL (ties into #51's per-node VOLL discussion).

### 8.2 Economics & environment (L2RPN + NESO-derived)
- Production cost vs. the **perfect-foresight oracle** for the same seeded day (same actuals, solved with hindsight): normalized 0 (do-nothing baseline) to 100 (oracle) exactly as L2RPN 2023 scores agents.
- Redispatch/curtailment costs itemized; emissions total (the NESO triad: frequency, cost, carbon).

### 8.3 The planner's mirror (purpose 5)
The report card ends with **"what planning missed"**: auto-generated findings from the shift, e.g. "ramp scarcity 17:10–17:40: the CEM valued capacity, not ramp — 340 MW of headroom existed but only 90 MW/10min of it was reachable" or "spin requirement met on paper but 60% sat behind the binding interface." Each finding links to the planning-view concept it challenges. This section is the bridge back to the planning curriculum and the seed for future planning-model evolution (operational-constraint-aware CEM).

## 9. Schema deltas (purpose 2 made concrete)

### 9.1 New entities (facet `rtops`)
| Entity | Fields (sketch) | Planning-schema analog? |
|---|---|---|
| `OperatingArea` | frequency_bias_mw_per_0.1hz (B), external_inertia, scheduled_interchange ref | none — planning has no "elsewhere" |
| `TieLine` (or flag on DCLine) | schedule_profile_id, ramp window | DCLine exists; *schedule* is new |
| `ProtectionSettings` | soft/hard overflow thresholds, overflow_steps_allowed, reconnection_steps | none — planning assumes ratings are never violated |
| `ForecastVintage` | kind (DA-hourly / ST-5min), error model params, issued_at | planning uses one deterministic series — vintage is the ops concept |
| `Clearance` | asset_id, window, recall_time, crew note | planning has retirement_year; ops has *this Tuesday, 09:00–15:00* |
| `ShiftScenario` | seed, date, weather day, scripted events, initial conditions | the planning Scenario names a *study*; this names a *day* |
| `TelemetryModel` (Phase 3) | per-measurement σ, scan rate, failure prob | none — planning data is exact by construction |
| `OperatorActionLog` / `DispatchInstruction` | runtime artifacts, exportable | none — planning outputs are decisions, not directives |

### 9.2 Extensions to existing entities
- `ReserveProduct`: response-time class (reg / 10-min spin / 30-min non-spin) and deployment semantics — currently it's a requirement, not a product with a clock.
- `Generator`: AGC-participation flag (regulation is narrower than `reserve_eligible`), quick-start flag + notification time.
- `ACLine`: the emergency/lt ratings become *live* semantics (protection tiers) instead of stored-but-unused.

### 9.3 The comparison payload
- Update `docs/sienna_comparison.md` + the schema atlas with an **operations coverage** band: Sienna has services/dynamic data and an explicit ops heritage (PSY grew out of production-cost + dynamics needs); PyPSA has none of telemetry/reserves/clearances (pure sidecar territory); glassbox-rtops sits in between by design.
- Add a **grid-rosetta experiment**: round-trip a `ShiftScenario`-bearing world through the PyPSA and Sienna hubs; the coverage manifests quantify "can planning schemas carry ops concepts?" — the interoperability purpose, measured not asserted.

## 10. Pedagogy: scenario library & challenges

Ship 5 seeded scenarios, each a `ShiftScenario` + a challenge entry in the existing ChallengesPanel, graded via §8:

1. **First shift (tutorial)** — benign day, guided tour, every panel explained on first alarm.
2. **The morning ramp** — under-forecast load + a sluggish committed fleet; pass = no BAAL violation through 09:00.
3. **DCS drill** — largest unit trips at 10:47 with no warning; pass = ACE recovered ≤ 15 min, reserves restored ≤ 90 min. SFR overlay teaches nadir vs. inertia (replay it with the planning slider: retire the coal unit, rerun, watch the nadir deepen — planning meets ops in one move).
4. **The 30-minute clock** — maintenance clearance active, RTCA flags a post-contingency overload; pass = violation cleared (redispatch or recall) inside 30 min without shedding.
5. **Storm shift** — weather event day: VRE collapse + elevated line outage rates + an EEA ladder; pass = survive to turnover with minimal firm shed, shed *proactively* if needed (scored better than protection-forced interruption).

Glossary gains ~30 terms (ACE, AGC, BAAL, basepoint, clearance, CPS1, DCS, EEA, HRUC, IROL, LMP already exists, nadir, RTCA, SCED, SE, SOL, TLR, three-part communication, …), each linked from the UI element where it first appears.

## 11. Verification (the oracle story, continued)

- **Determinism**: same seed ⇒ byte-identical event log and report (CI test).
- **Perfect-foresight oracle**: the scoring baseline is itself an engine run (UC+dispatch over actuals) — reuses the existing oracle harness pattern; sim cost must be ≥ oracle cost minus tolerance, always.
- **Conservation every tick**: generation + net imports − load − losses ≈ d(imbalance) accounting; any violation is a hard test failure.
- **SFR consistency**: the trip-event transient must match the dynamics engine run standalone on the same pre-trip state.
- **Doctrine tests**: DCS timer fires at exactly 15:00 min; interchange steps at :00 with the :50→:10 ramp; soft overflow trips on step N+1, not N.
- **Future (via grid-rosetta)**: translate a scenario to a Grid2Op environment and cross-check the protection/cascade sequence on the same chronics — two independent implementations of the same rulebook.

## 12. Explicitly out of scope (v1)

Per the research's "skip" guidance: busbar splitting / substation topology (Grid2Op's signature, too subtle for the intro tool), continuous AC/voltage-VAR operations (Phase 3: VAR-001 schedules using the existing Newton-Raphson engine), protection-relay detail (PRC is field engineering), EMT timescales, multi-user/multi-desk play, an RL/Gym API (natural future: glassbox-as-Grid2Op-backend through grid-rosetta), TLR levels 2–6 (single external area makes TLR mostly moot; keep the vocabulary in an RC event).

## 13. Phasing

- **Phase 0 — kernel (headless):** rtops engine, staged DA→RT with feedforward, actuals process, protection rules, forced outages, ACE/frequency emulation, deterministic replay, oracle + doctrine tests. *Exit: a scripted agent completes a seeded shift; all §11 tests green.*
- **Phase 1 — the room:** Control Room tab with all 10 panels, action bar with legality explanations, clock controls, shift report card. *Exit: a human completes scenario 1 end-to-end in the browser.*
- **Phase 2 — doctrine depth:** HRUC approvals, EEA ladder + RC directives, DCS/BAAL/CPS scoring finalized, clearances, instructor console, scenario library + challenges, glossary. *Exit: all 5 scenarios shippable and graded.*
- **Phase 3 — trust & volts:** telemetry noise + SE health (WLS on the AC model, bad-data drills, "flying blind" scenario), voltage schedules/VAR dispatch, restoration epilogue, rosetta ops-interop experiment, schema-atlas ops band.

## 14. Open questions

1. Single combined BA+TOP desk (proposed) vs. selectable roles per scenario?
2. ACE emulation cadence: 1 sim-second per AGC cycle (proposed) vs. honest 4-s cycles at 60× (240 ticks/min may stress the 1 Hz poll — SSE instead?).
3. Should blackout end the shift (Grid2Op purism) or always continue into restoration (proposed: continue — more is learned in the epilogue)?
4. Reuse the `ops` facet or introduce `rtops` (proposed: new facet; planning-`ops` is production-cost simulation, a different animal — and the naming distinction is itself curriculum).
5. LMP presentation during scarcity: full ORDC-style adders are out of scope, but should scarcity pricing appear at all (a simple reserve-shortage adder), or is VOLL-priced unserved enough for v1?

## 15. Sources (research grounding)

- NERC Reporting ACE definition & white paper; BAL-001-2 (CPS1/BAAL) + background doc; BAL-002-3 (DCS 15/90-min); BAL-003 (FRO); TOP-001 (30-min RTA); IRO-008/-009; EOP-011-4 (EEA-1/2/3); VAR-001-5; NERC SOC Program Manual (SOCCED) — nerc.com
- ERCOT: SE/RTCA cadence papers, RTM/SCED, RUC training materials — ercot.com
- CAISO AGC/telemetry requirements (4-s AGC) — caiso.com
- Grid2Op: `BaseObservation`/`BaseAction`/`Parameters` sources, MDP docs, opponent docs, L2RPN 2023 scoring — github.com/Grid2op/grid2op, grid2op.readthedocs.io
- NREL Sienna PowerSimulations.jl: sequences, feedforwards, emulation models — github.com/NREL-Sienna/PowerSimulations.jl
- gridfm-datakit load-scenario generation — github.com/gridfm/gridfm-datakit
- IMAGINARY powergrid-dynamics-simulation (frequency pedagogy) — imaginary.github.io/powergrid-dynamics-simulation
- Dispatcher Training Simulator feature sets (GE Vernova DTS; DTS literature) — gegridsolutions.com; Kent State DTS experiences paper
- NESO "Balancing the Grid" game (scoring triad) — neso.energy
- GRTOS (RTCA→SCED pipeline vocabulary) — github.com/rpglab/GRTOS

*Related issues: #47 (sortable results tables — the ops sim needs the same machinery live), #50 (server-side session avoids tab-switch state loss by construction), #51 (VOLL semantics), #52 (playback strip generalizes into the live control-room strip). Related repo: grid-rosetta (ops-concept interoperability experiments, §9.3).*
