// Typed client for the Glassbox API (Phase 0 surface).

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export interface WorldSummary {
  id: string;
  name: string;
  description: string;
  base_power_mva: number;
  base_frequency_hz: number;
  reference_bus_id: string;
  counts: Record<string, number>;
  n_dynamic_models: number;
  n_time_series: number;
  n_weather_years: number;
}

export interface FacetInfo {
  code: string;
  label: string;
  description: string;
  engine: string | null;
}

export interface LoadScope {
  id: string;
  name: string;
}

export interface AggregatedLoad {
  scope: string;
  unit: string;
  n_loads: number;
  start: number;
  length: number;
  downsample: number;
  values: number[];
}

export interface GraphNode {
  id: string;
  name: string;
  zone: string;
  x: number;
  y: number;
  base_kv: number;
  bus_type: string;
  attached: {
    generators: string[];
    loads: string[];
    storage: string[];
    hydro: string[];
  };
}

export interface GraphEdge {
  id: string;
  kind: string;
  from: string;
  to: string;
  rating_mva: number;
  is_candidate?: boolean;
  x?: number;
}

export interface GraphInterface {
  id: string;
  name: string;
  member_line_ids: string[];
  limit_mw: number;
  limit_source: string;
}

export interface GraphCandidate {
  id: string;
  name: string;
  kind: string; // generator | storage | line
  technology: string;
  bus_id: string | null;
  from_bus_id: string | null;
  to_bus_id: string | null;
  x: number;
  y: number;
  build_max_mw: number | null;
  capex_per_mw: number | null;
  lcoe_per_mwh: number | null;
  expected_capacity_factor: number | null;
}

export interface SupplyTranche {
  build_max_mw: number;
  capex_per_mw: number;
  expected_capacity_factor: number | null;
  lcoe_per_mwh: number | null;
}

export interface GraphResourcePotential {
  id: string;
  name: string;
  kind: string; // generator | storage
  technology: string;
  zone_id: string;
  bus_id: string | null;
  x: number;
  y: number;
  total_build_max_mw: number;
  tranches: SupplyTranche[];
  profile_ids: string[]; // availability profiles the curve draws on (links to the resource field)
}

export interface Terrain {
  land: [number, number][]; // seeded landmass polygon
  river: [number, number][]; // polyline through the hydro zone
  cities: { bus_id: string; name: string; x: number; y: number; size: number }[];
  resource_blobs: {
    kind: string; x: number; y: number; r: number; intensity: number;
    profile_id?: string; site_id?: string; quality?: number;
  }[];
  span: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  zones: { id: string; name: string; member_bus_ids: string[] }[];
  interfaces: GraphInterface[];
  candidates: GraphCandidate[];
  resource_potentials: GraphResourcePotential[];
  terrain?: Terrain;
}

// A solved run's spatial results, pushed onto the map by the Scenario Lab.
// Prices are keyed by bus id (nodal runs) or zone id (zonal runs — every bus
// in the zone shows the one flattened price, which is itself the lesson).
export interface MapResults {
  label: string; // e.g. "Nodal vs Zonal (capacity) — B (nodal)"
  scenario: "A" | "B";
  spatial: string; // identity | aggregate | ...
  layer: string; // cem | pcm | ...
  nodalPrice: Record<string, number>;
  flows: Record<string, number>; // line id -> time-averaged |flow| MW
  builtCapacity: Record<string, number>; // candidate id -> MW
  builtStoragePower: Record<string, number>;
  builtTransmission: Record<string, number>; // candidate line id -> MW
  builtResourcePotential: Record<string, number>; // supply curve id -> MW
  unservedMwh: Record<string, number>; // node id -> weighted MWh/yr unserved
  // chronological playback (issue #27) — aligned per-timestep series
  timesteps?: number[]; // absolute hours (t % 24 = hour of day)
  priceT?: Record<string, number[]>; // node -> $/MWh per timestep
  flowT?: Record<string, number[]>; // line -> signed MW per timestep
  unservedT?: Record<string, number[]>; // node -> MW shed per timestep
  stack?: { tech: string; series: number[] }[]; // dispatch by technology
}

export interface PerUnit {
  value: number;
  unit: string;
  note: string;
}

export interface InspectField {
  name: string;
  value: unknown;
  unit: string | null;
  base: string | null;
  facets: string[];
  description: string;
  per_unit: PerUnit | null;
}

export interface AttachedRef {
  collection: string;
  id: string;
  label: string;
  kind: string;
}

export interface InspectPayload {
  collection: string;
  type: string;
  id: string;
  facet: string | null;
  fields: InspectField[];
  attached: AttachedRef[];
}

export interface TimeSeriesMeta {
  id: string;
  kind: string;
  unit: string | null;
  years: number[];
  hours_per_year: number;
}

export interface TimeSeriesData {
  id: string;
  unit: string | null;
  kind: string;
  start: number;
  length: number;
  downsample: number;
  values: number[];
}

export interface ExplainPayload {
  title: string;
  formulation: { statement: string; symbolic: string[]; variables: string[] };
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  intermediates: Record<string, unknown>;
  provenance: Record<string, unknown>;
  information_loss: string[];
  map?: { id: string; kind: string; n_periods: number };
}

export interface GroundTruth {
  site_id: string;
  kind: string;
  unit: string | null;
  n_years: number;
  truth: { mean: number; std: number; bin_edges: number[]; density: number[] };
  per_year_means: number[];
  per_year_p5: number[];
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${path} -> ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export interface ScenarioPreset {
  key: string;
  name: string;
  lesson: string;
  a: Record<string, unknown>;
  b: Record<string, unknown>;
}

export interface ScenarioRunPayload {
  scenario: Record<string, unknown>;
  summary: Record<string, any>;
  result: Record<string, any>;
  explain: ExplainPayload;
  operator_explanations: Record<string, ExplainPayload>;
}

export interface DiffScalar {
  a: number;
  b: number;
  delta?: number;
  pct?: number | null;
}

export interface DiffPayload {
  a: { id: string; name: string; layer: string; spatial: string; weather_years: number[] };
  b: { id: string; name: string; layer: string; spatial: string; weather_years: number[] };
  scalars: Record<string, DiffScalar>;
  capacity_mix_mw: Record<string, DiffScalar>;
  nodal_prices?: Record<string, DiffScalar>;
  congestion?: Record<string, DiffScalar>;
  realized_capacity_factor?: Record<string, DiffScalar>;
}

export interface ScenarioDiffResult {
  a: ScenarioRunPayload;
  b: ScenarioRunPayload;
  diff: DiffPayload;
}

export interface OracleMetric {
  name: string;
  kernel: number | string;
  oracle: number | string;
  diff: number;
  unit: string;
  tol: number;
  why?: string; // what agreement/divergence on this metric means
}

export interface OracleResult {
  available: boolean;
  oracle: string;
  engine?: string;
  hour?: number;
  metrics?: OracleMetric[];
  converged_both?: boolean;
  n_buses?: number;
  detail?: Record<string, unknown>;
  failure?: string; // structured divergence (no more raw 500s)
  why?: string;
  note?: string | null;
  excluded?: Record<string, unknown>; // assets the oracle does NOT model
  scope_note?: string;
}

export interface WeatherEvent {
  key: string;
  name: string;
  description: string;
  kind: string;
  year: number;
  start_hour: number;
  duration_h: number;
  severity: number;
  scenario: Record<string, unknown>;
}

export const api = {
  worldSummary: () => get<WorldSummary>("/world/summary"),
  oracleAvailability: () => get<Record<string, boolean>>("/oracle/availability"),
  oraclePowerflow: () => get<OracleResult>("/oracle/powerflow"),
  oracleDispatch: () => get<OracleResult>("/oracle/dispatch"),
  // scenario-aware round-trips: validate the run the user actually made (#13)
  oraclePowerflowFor: (scenario: Record<string, unknown>) =>
    post<OracleResult>("/oracle/powerflow", { scenario }),
  oracleDispatchFor: (scenario: Record<string, unknown>) =>
    post<OracleResult>("/oracle/dispatch", { scenario }),
  // deep oracles (issue #14): multi-hour window + capacity expansion
  oracleDispatchWindow: () => get<OracleResult>("/oracle/dispatch_window"),
  oracleExpansion: () => get<OracleResult>("/oracle/expansion"),
  oracleDynamics: () => get<OracleResult>("/oracle/dynamics"),
  presets: () => get<ScenarioPreset[]>("/scenario/presets"),
  weatherEvents: () => get<WeatherEvent[]>("/weather/events"),
  // build mode (issue #28)
  placeCandidate: (body: Record<string, unknown>) =>
    post<{ created: string; name: string; collection: string;
           lcoe_per_mwh?: number | null; expected_capacity_factor?: number | null;
           capex_annual_per_mw?: number; note?: string | null }>("/world/candidates", body),
  resetWorld: () => post<{ ok: boolean }>("/world/reset", {}),
  // build mode v2 (issue #28): inline editing, journal, save-as
  patchEntity: (collection: string, id: string, fields: Record<string, unknown>) =>
    fetch(`${BASE}/world/${collection}/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail ?? `patch -> ${r.status}`);
      return r.json();
    }),
  deleteEntity: (collection: string, id: string) =>
    fetch(`${BASE}/world/${collection}/${id}`, { method: "DELETE" }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail ?? `delete -> ${r.status}`);
      return r.json();
    }),
  undoEdit: () => post<{ undone: string; can_undo: boolean; can_redo: boolean }>("/world/undo", {}),
  redoEdit: () => post<{ redone: string; can_undo: boolean; can_redo: boolean }>("/world/redo", {}),
  journalState: () =>
    get<{ can_undo: boolean; can_redo: boolean; undo_label: string | null;
          redo_label: string | null; n_edits: number }>("/world/journal"),
  saveWorld: (name: string) =>
    post<{ saved: string; hint: string }>("/world/save", { name }),
  runScenario: (scenario: Record<string, unknown>) =>
    post<ScenarioRunPayload>("/scenario/run", scenario),
  diffScenarios: (a: Record<string, unknown>, b: Record<string, unknown>) =>
    post<ScenarioDiffResult>("/scenario/diff", { a, b }),
  facets: () => get<FacetInfo[]>("/schema/facets"),
  graph: () => get<GraphData>("/graph"),
  listEntities: (collection: string) =>
    get<Record<string, unknown>[]>(`/entities/${collection}`),
  inspect: (collection: string, id: string, facet?: string) =>
    get<InspectPayload>(
      `/entity/${collection}/${id}${facet ? `?facet=${facet}` : ""}`,
    ),
  timeseriesList: () => get<TimeSeriesMeta[]>("/timeseries"),
  timeseries: (id: string, start = 0, length = 8760, downsample = 1) =>
    get<TimeSeriesData>(
      `/timeseries/${id}?start=${start}&length=${length}&downsample=${downsample}`,
    ),
  loadScopes: () => get<LoadScope[]>("/series/load-scopes"),
  aggregatedLoad: (scope: string, start = 0, length = 168, downsample = 1) =>
    get<AggregatedLoad>(
      `/series/load?scope=${scope}&start=${start}&length=${length}&downsample=${downsample}`,
    ),
  weatherSites: () => get<{ id: string; kind: string; name: string }[]>("/weather/sites"),
  groundTruth: (siteId: string, kind = "availability") =>
    get<GroundTruth>(`/weather/ground-truth/${siteId}?kind=${kind}`),
  attributeExplain: (facet: string) =>
    get<ExplainPayload>(`/operators/attribute/${facet}/explain`),
  spatialExplain: (mode: string) =>
    get<ExplainPayload>(`/operators/spatial/${mode}/explain`),
  temporalExplain: (kind: string, nDays = 12) =>
    get<ExplainPayload>(`/operators/temporal/explain?kind=${kind}&n_days=${nDays}`),

  // --- translate: import/export via grid-rosetta (issue #53) ---
  translateAvailability: () => get<TranslateAvailability>("/translate/availability"),
  translateImport: (req: TranslateImportRequest) =>
    post<TranslateImportResult>("/translate/import", req),
  translateExport: (req: TranslateExportRequest) =>
    post<TranslateExportResult>("/translate/export", req),
};

// --- translate types (issue #53) ---

export interface TranslateAvailability {
  available: boolean;
  reason?: string;
  rosetta_version?: string;
  schemas?: string[];
  importable_from?: string[];
  hubs?: string[];
  bridges?: string[];
  solvers?: string[];
}

export interface TranslateImportRequest {
  source: string;
  schema_name: string;
  hub?: string | null;
  hours?: number;
}

export interface CoverageHop {
  bridge: string;
  translated: Record<string, number>;
  approximated: { what: string; how: string }[];
  parked: { concept: string; n?: number; why: string }[];
  restored: { concept: string; n?: number }[];
  dropped: { what: string; why: string }[];
  invented: { what: string; value: unknown; why: string }[];
  manual_mapping_required: { entity: string; label: string; note: string }[];
}

export interface CoverageManifest {
  route: string[];
  hops: CoverageHop[];
  totals: Record<string, number>;
  sidecar_remaining: number;
}

export interface SidecarEntrySummary {
  concept: string;
  entity_id: string;
  reason: string;
}

export interface TranslateImportResult {
  imported: boolean;
  world: { id: string; name: string; counts: Record<string, number> };
  manifest: CoverageManifest;
  sidecar: SidecarEntrySummary[];
}

export interface TranslateExportRequest {
  schema_name: string;
  hub?: string | null;
  name?: string;
  hours?: number;
}

export interface TranslateExportResult {
  exported: string;
  manifest: CoverageManifest;
  sidecar_remaining: SidecarEntrySummary[];
}

// Facet -> which World collections it meaningfully consumes, for the canvas.
export const COLLECTIONS = [
  "buses",
  "zones",
  "ac_lines",
  "transformers",
  "dc_lines",
  "shunts",
  "interfaces",
  "generators",
  "hydro_units",
  "storage_units",
  "loads",
  "expansion_candidates",
  "resource_potentials",
  "fuels",
  "cost_curves",
  "policies",
  "reserve_products",
  "system_constraints",
  "disturbances",
];
