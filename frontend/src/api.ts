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

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  zones: { id: string; name: string; member_bus_ids: string[] }[];
  interfaces: GraphInterface[];
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
}

export interface OracleResult {
  available: boolean;
  oracle: string;
  engine?: string;
  hour?: number;
  metrics?: OracleMetric[];
  converged_both?: boolean;
  n_buses?: number;
  detail?: Record<string, number>;
}

export const api = {
  worldSummary: () => get<WorldSummary>("/world/summary"),
  oracleAvailability: () => get<Record<string, boolean>>("/oracle/availability"),
  oraclePowerflow: () => get<OracleResult>("/oracle/powerflow"),
  oracleDispatch: () => get<OracleResult>("/oracle/dispatch"),
  oracleDynamics: () => get<OracleResult>("/oracle/dynamics"),
  presets: () => get<ScenarioPreset[]>("/scenario/presets"),
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
};

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
  "fuels",
  "cost_curves",
  "policies",
  "reserve_products",
  "system_constraints",
  "disturbances",
];
