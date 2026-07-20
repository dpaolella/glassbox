// Plain-language definitions for the power-systems jargon shown in the UI.
// Used as hover captions (title attributes) on legends, overlays, the catalog,
// and bus badges so a user who doesn't know the terms isn't left guessing.

export const GLOSSARY: Record<string, string> = {
  // bus / node concepts
  slack:
    "Slack (reference) bus: the single bus that holds voltage angle = 0 and " +
    "balances any leftover generation/load mismatch and losses in a power flow.",
  pv_bus:
    "PV bus: a bus with a generator that regulates its voltage magnitude; the " +
    "generator's reactive power is solved for.",
  pq_bus:
    "PQ bus: a bus with fixed real and reactive injection (most load buses); " +
    "its voltage magnitude and angle are solved for.",

  // lines / branches
  ac_line: "AC transmission line (π-model: series R+jX with line charging B).",
  transformer:
    "Transformer: couples two voltage levels; modeled as a series impedance " +
    "with an optional tap ratio / phase shift.",
  dc_line:
    "HVDC link: a controllable point-to-point DC transfer, set independently " +
    "of the AC network.",
  candidate:
    "Candidate line: a transmission upgrade the capacity-expansion engine may " +
    "choose to build (it isn't in the base network yet).",
  weak_feeder:
    "Weak feeder (low short-circuit ratio): a high-impedance, lightly-meshed " +
    "connection. Inverter-based resources there are prone to control-driven " +
    "instability — the pocket the EMT layer studies.",

  // groupings
  zone:
    "Zone: a geographic region of the grid. Economic layers can aggregate the " +
    "nodal network down to zones (which hides intra-zonal congestion).",
  interface:
    "Interface (flowgate): a monitored cut of lines with an aggregate transfer " +
    "limit — e.g. the tie between two regions. Its limit can come from thermal " +
    "ratings or from a stability study.",

  // devices
  generators: "Generators attached to this bus (thermal, nuclear, wind, solar…).",
  storage: "Storage units (batteries, pumped hydro) attached to this bus.",
  loads: "Electrical demand attached to this bus.",
  hydro: "Hydro units (reservoir, run-of-river, pumped) attached to this bus.",

  // investment / resource potential
  resource_potential:
    "Resource Potential (zonal): a supply curve of buildable resource across a " +
    "whole zone — the best sites are cheapest, so cost rises in steps (tranches). " +
    "Early-screening granularity. CEM builds tranches cheapest-first up to the " +
    "zone's potential. Only the capacity-expansion (inv) layer sees these.",
  resource_potentials:
    "Resource Potential (zonal): a stepped supply curve of how much of a " +
    "technology a zone could host and at what rising cost. Distinct from a " +
    "node-specific candidate plant.",
  supply_curve:
    "Supply curve: buildable capacity ordered cheapest-first. Each step (tranche) " +
    "is a block of MW at its own $/MW — better sites are used before costlier ones.",
  expansion_candidates:
    "Expansion candidates (nodal): specific buildable plants/lines at specific " +
    "buses (capex, build limit, operating template) — 'should we build this here?'. " +
    "Distinct from a zonal resource-potential supply curve.",
  candidate_nodal:
    "A node-specific build option: a concrete plant, battery, or line at a given " +
    "bus that the CEM can choose to build.",

  // catalog collections
  shunts: "Shunt devices: fixed or controllable reactive compensation (capacitors/reactors, SVC/STATCOM).",
  fuels: "Fuels: prices and CO₂ content used to cost thermal generation.",
  cost_curves: "Cost curves: piecewise-linear marginal cost vs output for a generator.",
  policies: "Policies: carbon price, emissions cap, RPS/CES, planning reserve margin.",
  reserve_products: "Reserve products: operating reserves (spinning, non-spinning, regulation, fast frequency response).",
  system_constraints: "System constraints: min inertia, min synchronous units, RoCoF limit, system strength — fed up from the dynamics layer.",
  disturbances: "Disturbances: contingencies (element outages) and faults used by power flow, dynamics and EMT.",

  // --- control room / real-time operations (issue #57) ---
  ace:
    "Area Control Error: (actual - scheduled interchange) - 10B(actual - " +
    "scheduled frequency). The balancing signal AGC drives toward zero; " +
    "negative = under-generating (leaning on the interconnection).",
  agc:
    "Automatic Generation Control: the EMS loop (every ~4 s in real life) " +
    "that nudges regulating units to chase ACE toward zero.",
  baal:
    "Balancing Authority ACE Limit (BAL-001-2): a dynamic ACE bound that " +
    "tightens as frequency strays; exceeding it for more than 30 " +
    "consecutive minutes is a violation.",
  basepoint:
    "The MW setpoint the real-time market (SCED) sends each unit every " +
    "interval; AGC adjusts around it.",
  breaker:
    "Circuit breaker: the switching device that can interrupt load and " +
    "fault current. Contrast with a disconnector.",
  breaker_failure:
    "When a breaker's mechanism fails to open on command, protection " +
    "escalates and clears the entire busbar section behind it.",
  busbar_section:
    "The physical bus inside a substation — conductor equipment occupying " +
    "a connectivity node (CIM's key node-breaker insight: the bus is " +
    "equipment, not a node).",
  clearance:
    "Authorization for a field crew to work on equipment, granted only " +
    "after visible isolation (breakers open, then disconnectors open, " +
    "then tagged).",
  connectivity_node:
    "CIM ConnectivityNode: an authored connection point inside a " +
    "substation; topology processing groups them into power-flow buses " +
    "across closed switches.",
  cps1:
    "Control Performance Standard 1 (BAL-001): a 100-plus-percent score " +
    "measuring whether your ACE helps or hurts interconnection frequency.",
  dcs:
    "Disturbance Control Standard (BAL-002): after losing a big unit, " +
    "recover ACE within 15 minutes and restore reserves within 90.",
  disconnector:
    "Disconnector (isolator): provides visible isolation but cannot " +
    "interrupt load current — it may only operate de-energized, which the " +
    "interlocks enforce.",
  eea:
    "Energy Emergency Alert (EOP-011): the RC-declared ladder — EEA-1 " +
    "(reserves below requirement), EEA-2 (deficient; load management), " +
    "EEA-3 (firm load interruption imminent or in progress).",
  interchange:
    "Net scheduled/actual power flowing to neighboring areas over the " +
    "ties. Schedules change hourly, ramped from :50 to :10.",
  node_breaker:
    "The operations-grade network model where every breaker and " +
    "disconnector is explicit; the planning bus-branch model is DERIVED " +
    "from it by topology processing.",
  ordc:
    "Operating Reserve Demand Curve: prices reserve shortage on a rising " +
    "curve so energy prices scream BEFORE load is shed.",
  rtca:
    "Real-Time Contingency Analysis: after each state solution, screen " +
    "N-1 outages and flag post-contingency violations.",
  sced:
    "Security-Constrained Economic Dispatch: the real-time market solve " +
    "producing basepoints and prices every interval.",
  scarcity_adder:
    "The reserve-shortage price that couples into the energy price when " +
    "reserves run short — the market recruiting help before shedding.",
  sol_clock:
    "TOP-001: a post-contingency System Operating Limit exceedance must " +
    "be mitigated within 30 minutes; the clock in the Control Room " +
    "counts it down.",
  stuck_breaker:
    "A breaker whose mechanism fails on command; the classic drill where " +
    "a routine switching order becomes an incident.",
  system_lambda:
    "The marginal cost of the marginal dispatched unit each interval — " +
    "the system energy price (plus any scarcity adder).",
  topology_processing:
    "The EMS's first act after any switching: collapse connectivity " +
    "nodes across closed switches into topological nodes (buses). A " +
    "power-flow bus is computed, never stored.",
};

export function gloss(key: string): string | undefined {
  return GLOSSARY[key];
}

// Field-level definitions, keyed by the entity field name. Surfaced as a hover
// caption on every inspector row (falling back to the schema's own description).
// Written for someone who may not know the jargon (e.g. what p_min_pu means).
export const FIELD_GLOSSARY: Record<string, string> = {
  // identity / siting
  id: "Unique identifier of this entity.",
  name: "Human-readable name.",
  kind: "Which kind of build option this is: generator, storage, or line.",
  technology: "Technology type (e.g. ccgt, wind, solar_pv, battery).",
  zone_id: "The zone (aggregation region) this belongs to.",
  bus_id: "The bus (node) this device connects to.",
  from_bus_id: "The 'from' end bus of this branch/line.",
  to_bus_id: "The 'to' end bus of this branch/line.",
  base_kv: "Nominal voltage of the bus, in kilovolts.",
  resource_class: "Resource quality band (e.g. wind/solar class) for this option.",

  // capacity & build limits
  p_max_mw: "Maximum (nameplate) real-power output, in megawatts.",
  build_min_mw: "Minimum that must be built if this option is selected (MW).",
  build_max_mw:
    "Maximum buildable capacity — the resource ceiling / potential at this site or step (MW).",
  total_build_max_mw: "Total buildable potential across all supply-curve steps (MW).",

  // economics
  capex_per_mw:
    "Overnight capital cost per MW of capacity. The engine annualizes it with a " +
    "capital-recovery factor (interest + lifetime) before adding it to cost.",
  capex_per_mwh:
    "Capital cost per MWh of storage energy capacity (sized separately from power).",
  fom_per_mw_yr:
    "Fixed operations & maintenance cost per MW per year — paid whether or not the unit runs.",
  vom_per_mwh:
    "Variable O&M cost per MWh generated — the per-unit-of-output running cost (excludes fuel).",
  lifetime_yr: "Economic life in years, used to annualize the capital cost.",
  lcoe_per_mwh:
    "Levelized cost of energy: all-in $/MWh (annualized capex + FOM spread over expected " +
    "output, plus variable cost). A rough screening metric, shown for context.",
  expected_capacity_factor:
    "Expected average output ÷ nameplate (0–1). Higher = better site. Display only — the " +
    "engine derives realized capacity factor from the hourly availability profile.",
  start_cost: "Cost incurred each time the unit starts up.",
  no_load_cost: "Cost per hour to keep the unit online at zero output (idling).",

  // operating template
  heat_rate_mmbtu_per_mwh:
    "Fuel efficiency: MMBtu of fuel burned per MWh produced. Lower = more efficient. " +
    "Multiplied by fuel price to get fuel cost per MWh.",
  fuel_id: "Which fuel this unit burns (links to its price and CO₂ content).",
  p_min_pu:
    "Minimum stable output as a fraction of capacity (per-unit, 0–1). A thermal unit that " +
    "is online can't turn down below this; e.g. 0.4 means it must run at ≥40% when committed.",
  availability_profile_id:
    "Time series (0–1 per hour) capping this resource's output — e.g. wind/solar availability.",
  ramp_up_mw_per_min: "How fast output can increase, in MW per minute.",
  ramp_down_mw_per_min: "How fast output can decrease, in MW per minute.",
  min_up_time_h: "Once started, the minimum hours the unit must stay online.",
  min_down_time_h: "Once stopped, the minimum hours before it can restart.",
  reserve_eligible: "Whether this unit can provide operating reserves.",

  // storage
  duration_h:
    "Energy-to-power ratio in hours: how long the store can discharge at full power " +
    "(energy MWh ÷ power MW).",
  efficiency_charge: "Fraction of energy retained when charging (0–1).",
  efficiency_discharge: "Fraction of energy retained when discharging (0–1).",
  soc_min_pu: "Minimum state of charge as a fraction of energy capacity.",
  soc_max_pu: "Maximum state of charge as a fraction of energy capacity.",

  // electrical
  reactance_pu: "Series reactance in per-unit on the system base — sets DC power-flow sharing.",
  r: "Series resistance (per-unit on the system base).",
  x: "Series reactance (per-unit on the system base). Higher = weaker/longer line.",
  b: "Total line charging susceptance (per-unit).",
  rating_normal_mva: "Continuous thermal rating of the line/branch (MVA).",

  // supply curve
  tranches:
    "The supply-curve steps: each is a block of MW at its own (rising) cost — best sites first.",
};
