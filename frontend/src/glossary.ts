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

  // catalog collections
  shunts: "Shunt devices: fixed or controllable reactive compensation (capacitors/reactors, SVC/STATCOM).",
  fuels: "Fuels: prices and CO₂ content used to cost thermal generation.",
  cost_curves: "Cost curves: piecewise-linear marginal cost vs output for a generator.",
  policies: "Policies: carbon price, emissions cap, RPS/CES, planning reserve margin.",
  reserve_products: "Reserve products: operating reserves (spinning, non-spinning, regulation, fast frequency response).",
  system_constraints: "System constraints: min inertia, min synchronous units, RoCoF limit, system strength — fed up from the dynamics layer.",
  disturbances: "Disturbances: contingencies (element outages) and faults used by power flow, dynamics and EMT.",
};

export function gloss(key: string): string | undefined {
  return GLOSSARY[key];
}
