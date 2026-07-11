import { useEffect, useState } from "react";
import {
  api,
  DiffScalar,
  ExplainPayload,
  MapResults,
  ScenarioDiffResult,
  ScenarioPreset,
} from "../api";
import { SCENARIO_A, SCENARIO_B } from "../theme";
import { Plot } from "./Plot";

// Pull the spatial results out of one run payload for the map (issue #17).
function extractMapResults(
  run: ScenarioDiffResult["a"],
  meta: { id: string; spatial: string; layer: string },
  which: "A" | "B",
  presetName: string,
): MapResults | null {
  const r = run.result as Record<string, any>;
  const network = r?.network;
  if (!network || !network.nodal_price) return null;
  // weighted unserved MWh per node (period weights annualize rep-period hours)
  const disp = r.operational ?? r.dispatch;
  const unservedMwh: Record<string, number> = {};
  if (disp?.unserved_mw) {
    const w: number[] = disp.period_weights ?? [];
    for (const [node, series] of Object.entries(disp.unserved_mw as Record<string, number[]>)) {
      const total = series.reduce(
        (acc: number, v: number, i: number) => acc + v * (w[i] ?? 1),
        0,
      );
      if (total > 1) unservedMwh[node] = total;
    }
  }
  return {
    unservedMwh,
    label: `${presetName} — ${which} (${meta.spatial === "identity" ? "nodal" : meta.spatial === "aggregate" ? "zonal" : meta.spatial})`,
    scenario: which,
    spatial: meta.spatial,
    layer: meta.layer,
    nodalPrice: network.nodal_price ?? {},
    flows: network.flow_mw ?? {},
    builtCapacity: r.built_capacity_mw ?? {},
    builtStoragePower: r.built_storage_power_mw ?? {},
    builtTransmission: r.built_transmission_mw ?? {},
    builtResourcePotential: r.built_resource_potential_mw ?? {},
  };
}

function fmt(n: number | undefined): string {
  if (n === undefined || n === null) return "—";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + "k";
  if (Math.abs(n) >= 100) return n.toFixed(0);
  if (Math.abs(n) >= 1) return Number.isInteger(n) ? String(n) : n.toFixed(1);
  return Number.isInteger(n) ? String(n) : n.toFixed(3);
}

// Per-metric label, unit, direction semantics, and definition (issue #18).
// `better` drives the delta color: for "lower" metrics an increase is bad.
const METRICS: Record<
  string,
  { label: string; unit: string; better: "lower" | "higher" | "neutral"; tip: string }
> = {
  total_cost: {
    label: "total system cost", unit: "$/yr", better: "lower",
    tip: "Annualized investment + operating cost (weighted over representative periods).",
  },
  vre_penetration: {
    label: "VRE penetration", unit: "fraction", better: "higher",
    tip: "Wind + solar energy as a share of total generation (0–1).",
  },
  curtailment_mwh_weighted: {
    label: "curtailment", unit: "MWh/yr", better: "lower",
    tip: "Available wind/solar energy thrown away (annualized via representative-period weights).",
  },
  unserved_mwh_weighted: {
    label: "unserved energy", unit: "MWh/yr", better: "lower",
    tip: "Load that could not be served (annualized via representative-period weights). Priced at VOLL in the objective — this is what drives scarcity prices.",
  },
  avg_price: {
    label: "avg price", unit: "$/MWh", better: "neutral",
    tip: "Time- and bus-averaged marginal price (dual of the energy balance). Scarcity hours at VOLL can dominate this average.",
  },
  price_spread: {
    label: "price spread", unit: "$/MWh", better: "neutral",
    tip: "Max minus min average bus price — nonzero spread means congestion separates locations.",
  },
  lole_hours_per_year: {
    label: "LOLE", unit: "h/yr", better: "lower",
    tip: "Loss-of-load expectation: expected hours per year with unserved load.",
  },
  eue_mwh_per_year: {
    label: "EUE", unit: "MWh/yr", better: "lower",
    tip: "Expected unserved energy per year.",
  },
  losses_mw: { label: "losses", unit: "MW", better: "lower", tip: "Network real-power losses." },
  frequency_nadir_hz: {
    label: "frequency nadir", unit: "Hz", better: "higher",
    tip: "Lowest system frequency after the disturbance — closer to 60 Hz is better.",
  },
  rocof_hz_per_s: {
    label: "RoCoF", unit: "Hz/s", better: "lower",
    tip: "Max rate of change of frequency — high RoCoF trips protection.",
  },
  damping_ratio: {
    label: "damping ratio", unit: "", better: "higher",
    tip: "How quickly oscillations decay; higher is more stable.",
  },
};

function metricOf(key: string) {
  return (
    METRICS[key] ?? {
      label: key.replace(/_/g, " "), unit: "", better: "neutral" as const,
      tip: "",
    }
  );
}

function ScalarRow({ k, d }: { k: string; d: DiffScalar }) {
  const m = metricOf(k);
  const up = (d.delta ?? 0) > 0;
  const deltaColor =
    m.better === "neutral" || (d.delta ?? 0) === 0
      ? "var(--muted)"
      : (m.better === "lower") === up
        ? "var(--warn)"
        : "var(--good)";
  return (
    <tr>
      <td className="field-name">
        <span className={m.tip ? "has-tip" : ""} title={m.tip}>{m.label}</span>
        {m.unit && <span className="unit"> {m.unit}</span>}
      </td>
      <td className="field-value">{fmt(d.a)}</td>
      <td className="field-value">{fmt(d.b)}</td>
      <td className="field-value" style={{ color: deltaColor }}
        title={m.better === "neutral" ? undefined : `for this metric, ${m.better} is better`}>
        {(d.delta ?? 0) >= 0 ? "+" : ""}
        {fmt(d.delta)}
      </td>
    </tr>
  );
}

const spatialName = (s: string) =>
  s === "identity" ? "nodal" : s === "aggregate" ? "zonal" : s;
const LAYER_NAME: Record<string, string> = {
  cem: "capacity expansion", pcm: "production cost", ra: "resource adequacy",
  pf: "power flow", dyn: "dynamics", emt: "EMT",
};

// Auto-interpretation (issue #19): turn the diff numbers into the lesson's
// argument. Preset-aware where it matters, generic fallback otherwise.
function interpret(diff: ScenarioDiffResult["diff"], presetKey?: string): string[] {
  const s = diff.scalars;
  const out: string[] = [];
  const g = (k: string) => s[k];
  const spatialDiffers = diff.a.spatial !== diff.b.spatial;
  if (spatialDiffers && g("price_spread")) {
    const d = g("price_spread");
    out.push(
      `Under the ${spatialName(diff.a.spatial)} view (A) the price spread is $${fmt(d.a)}/MWh; the ${spatialName(diff.b.spatial)} view (B) reveals $${fmt(d.b)}/MWh. That spread is the congestion the aggregated view averages away.`,
    );
  }
  if (spatialDiffers && g("curtailment_mwh_weighted")) {
    const d = g("curtailment_mwh_weighted");
    if ((d.delta ?? 0) > 1)
      out.push(
        `Curtailment rises from ${fmt(d.a)} to ${fmt(d.b)} MWh/yr under the finer view — remote renewables get stranded behind the constrained corridor that the coarse view pretends isn't there.`,
      );
  }
  if (g("unserved_mwh_weighted") && (g("unserved_mwh_weighted").b ?? 0) > 1) {
    out.push(
      `Scenario B leaves ${fmt(g("unserved_mwh_weighted").b)} MWh/yr unserved (red rings on the map). Those hours are priced at VOLL, which is why avg price looks extreme.`,
    );
  }
  if (g("total_cost") && Math.abs(g("total_cost").delta ?? 0) > 1) {
    const d = g("total_cost");
    out.push(
      `Total system cost moves from $${fmt(d.a)} to $${fmt(d.b)}/yr (${(d.delta ?? 0) > 0 ? "+" : ""}$${fmt(d.delta)}). ${spatialDiffers ? "The cheaper-looking coarse plan is not a real plan — it books power deliveries the grid can't physically make." : ""}`,
    );
  }
  if (g("lole_hours_per_year")) {
    const d = g("lole_hours_per_year");
    out.push(
      `LOLE goes ${fmt(d.a)} → ${fmt(d.b)} h/yr: sampling more weather years exposes tail risk a single benign year hides.`,
    );
  }
  if (!out.length) {
    // generic: the two largest relative moves
    const ranked = Object.entries(s)
      .filter(([, d]) => isFinite(d.a) && isFinite(d.b) && Math.abs(d.a) > 1e-9)
      .map(([k, d]) => [k, Math.abs((d.delta ?? 0) / d.a)] as [string, number])
      .sort((x, y) => y[1] - x[1])
      .slice(0, 2);
    for (const [k] of ranked) {
      const d = s[k];
      out.push(`${metricOf(k).label}: ${fmt(d.a)} → ${fmt(d.b)} ${metricOf(k).unit}.`);
    }
  }
  void presetKey;
  return out;
}

function EngineMath({ explain }: { explain: ExplainPayload }) {
  return (
    <details className="engine-math">
      <summary>{explain.title}</summary>
      {explain.formulation.statement && (
        <p className="formulation">{explain.formulation.statement}</p>
      )}
      <pre className="symbolic">{explain.formulation.symbolic.join("\n")}</pre>
      <details>
        <summary>inputs / outputs / intermediates</summary>
        <pre className="json">
          {JSON.stringify(
            {
              inputs: explain.inputs,
              outputs: explain.outputs,
              intermediates: explain.intermediates,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </details>
  );
}

interface LabProps {
  layerLabel: string;
  layerEngine: string | null; // cem | pcm | ra | pf | dyn | emt | null (core)
  onPickLayer: (code: string) => void;
  onMapResults: (r: MapResults | null) => void;
  mapResults: MapResults | null;
}

// modeling-layer code -> the scenario engine its experiments use
const LAYER_FOR_ENGINE: Record<string, string> = {
  cem: "inv",
  pcm: "ops",
  ra: "adq",
  pf: "pf",
  dyn: "dyn",
  emt: "emt",
};

export function ScenarioLab({
  layerLabel,
  layerEngine,
  onPickLayer,
  onMapResults,
  mapResults,
}: LabProps) {
  const [presets, setPresets] = useState<ScenarioPreset[]>([]);
  const [active, setActive] = useState<ScenarioPreset | null>(null);
  const [result, setResult] = useState<ScenarioDiffResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.presets().then(setPresets).catch((e) => setErr(String(e)));
  }, []);

  // show only the experiments for the selected modeling layer, so the Scenario
  // tab is consistent with the layer chip at the top (and every other tab)
  const shown = presets.filter(
    (p) => !layerEngine || (p.a.layer as string) === layerEngine,
  );
  useEffect(() => {
    // drop a stale active preset if it no longer belongs to the chosen layer
    if (active && layerEngine && (active.a.layer as string) !== layerEngine) {
      setActive(null);
      setResult(null);
    }
  }, [layerEngine, active]);

  function run(p: ScenarioPreset) {
    setActive(p);
    setResult(null);
    setErr(null);
    setLoading(true);
    api
      .diffScenarios(p.a, p.b)
      .then((res) => {
        setResult(res);
        // paint scenario B on the map by default — in the presets B is the
        // higher-fidelity "reveal" run (nodal, many-years, with-policy)
        const mb = extractMapResults(res.b, res.diff.b, "B", p.name);
        onMapResults(mb ?? extractMapResults(res.a, res.diff.a, "A", p.name));
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }

  const diff = result?.diff;
  const mixKeys = diff
    ? Object.keys(diff.capacity_mix_mw).filter(
        (k) => diff.capacity_mix_mw[k].a > 0 || diff.capacity_mix_mw[k].b > 0,
      )
    : [];

  const otherEngineWithPresets = presets.find(
    (p) => LAYER_FOR_ENGINE[p.a.layer as string],
  );

  return (
    <div className="scenario-lab">
      <p className="muted">
        Experiments for the <b>{layerLabel}</b> layer. Each is a pair of
        scenarios differing in exactly one operator or override (Section 10);
        run one to see the diff. Switch the modeling layer at the top to see
        other experiments.
      </p>

      {shown.length === 0 ? (
        <div className="lesson-box">
          The <b>{layerLabel}</b> layer has no scenario experiments
          {layerEngine === null
            ? " — it is the shared topology layer. Pick a modeling layer (capacity expansion, operations, adequacy, power flow, dynamics, EMT) to run experiments."
            : "."}
          {otherEngineWithPresets && (
            <div style={{ marginTop: 6 }}>
              <button
                className="preset"
                onClick={() =>
                  onPickLayer(LAYER_FOR_ENGINE[otherEngineWithPresets.a.layer as string])
                }
              >
                jump to {otherEngineWithPresets.name}
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="preset-list">
          {shown.map((p) => (
            <button
              key={p.key}
              className={`preset ${active?.key === p.key ? "active" : ""}`}
              onClick={() => run(p)}
            >
              {p.name}
            </button>
          ))}
        </div>
      )}

      {active && <div className="lesson-box">{active.lesson}</div>}
      {loading && <div className="empty-hint">solving both scenarios…</div>}
      {err && <div className="error-banner">{err}</div>}

      {diff && (
        <>
          <div className="diff-head"
            title="Two runs of the same world that differ in exactly one modeling choice. A is the coarser/baseline run, B the higher-fidelity or with-policy run.">
            <span className="diff-a">
              A — {spatialName(diff.a.spatial)} · {LAYER_NAME[diff.a.layer] ?? diff.a.layer} ·
              weather yr {diff.a.weather_years.join(",")}
            </span>
            <span className="diff-b">
              B — {spatialName(diff.b.spatial)} · {LAYER_NAME[diff.b.layer] ?? diff.b.layer} ·
              weather yr {diff.b.weather_years.join(",")}
            </span>
          </div>

          {interpret(diff, active?.key).length > 0 && (
            <div className="interpret-box">
              <div className="interpret-title">what the numbers say</div>
              {interpret(diff, active?.key).map((line, i) => (
                <p key={i}>{line}</p>
              ))}
            </div>
          )}

          {(extractMapResults(result.a, diff.a, "A", active?.name ?? "") ||
            extractMapResults(result.b, diff.b, "B", active?.name ?? "")) && (
            <div className="map-push">
              <span className="muted">on the map:</span>
              {(["A", "B"] as const).map((which) => {
                const run = which === "A" ? result.a : result.b;
                const meta = which === "A" ? diff.a : diff.b;
                const mr = extractMapResults(run, meta, which, active?.name ?? "");
                if (!mr) return null;
                const on = mapResults?.scenario === which && mapResults?.label === mr.label;
                return (
                  <button
                    key={which}
                    className={`map-push-btn ${on ? "active" : ""}`}
                    style={{ borderColor: which === "A" ? SCENARIO_A : SCENARIO_B }}
                    title={`Paint scenario ${which}'s prices, flows and builds on the map`}
                    onClick={() => onMapResults(mr)}
                  >
                    {which} · {meta.spatial === "identity" ? "nodal" : meta.spatial === "aggregate" ? "zonal" : meta.spatial}
                  </button>
                );
              })}
              {mapResults && (
                <button className="map-push-btn" onClick={() => onMapResults(null)}
                  title="Remove results from the map">
                  clear
                </button>
              )}
            </div>
          )}

          <table className="field-table">
            <thead>
              <tr>
                <th></th>
                <th className="field-value">A</th>
                <th className="field-value">B</th>
                <th className="field-value">Δ</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(diff.scalars).map(([k, d]) => (
                <ScalarRow key={k} k={k} d={d} />
              ))}
            </tbody>
          </table>

          {mixKeys.length > 0 && (
            <>
              <h4>Capacity mix (MW)</h4>
              <Plot
                height={240}
                data={[
                  {
                    type: "bar",
                    name: "A",
                    x: mixKeys,
                    y: mixKeys.map((k) => diff.capacity_mix_mw[k].a),
                    marker: { color: SCENARIO_A },
                  },
                  {
                    type: "bar",
                    name: "B",
                    x: mixKeys,
                    y: mixKeys.map((k) => diff.capacity_mix_mw[k].b),
                    marker: { color: SCENARIO_B },
                  },
                ]}
                layout={{ barmode: "group", yaxis: { title: "MW" } }}
              />
            </>
          )}

          {diff.nodal_prices && Object.keys(diff.nodal_prices).length > 0 && (
            <>
              <h4>Nodal / zonal prices ($/MWh)</h4>
              <Plot
                height={240}
                data={[
                  {
                    type: "bar",
                    name: "A",
                    x: Object.keys(diff.nodal_prices),
                    y: Object.values(diff.nodal_prices).map((d) => d.a),
                    marker: { color: SCENARIO_A },
                  },
                  {
                    type: "bar",
                    name: "B",
                    x: Object.keys(diff.nodal_prices),
                    y: Object.values(diff.nodal_prices).map((d) => d.b),
                    marker: { color: SCENARIO_B },
                  },
                ]}
                layout={{ barmode: "group", yaxis: { title: "$/MWh" } }}
              />
            </>
          )}

          {result && <ImpedanceScans result={result} />}

          <h4>Engine math (the transparency contract)</h4>
          {result && (
            <>
              <EngineMath explain={result.a.explain} />
              <EngineMath explain={result.b.explain} />
            </>
          )}
        </>
      )}
    </div>
  );
}

// EMT impedance scan (|Z| vs frequency) with resonance peaks marked.
function ImpedanceScans({ result }: { result: ScenarioDiffResult }) {
  const scanOf = (r: ScenarioDiffResult["a"]) => r.result?.impedance_scan;
  const a = scanOf(result.a);
  const b = scanOf(result.b);
  if (!a && !b) return null;

  const traces: Record<string, unknown>[] = [];
  const mark = (scan: any, name: string, color: string) => {
    if (!scan) return;
    const mag = scan.frequency_hz.map((_: number, i: number) =>
      Math.hypot(scan.impedance_real[i], scan.impedance_imag[i]),
    );
    traces.push({
      type: "scatter", mode: "lines", name, x: scan.frequency_hz, y: mag,
      line: { color },
    });
  };
  mark(a, "A |Z|", SCENARIO_A);
  mark(b, "B |Z|", SCENARIO_B);

  const peaks = [
    ...(a?.resonance_peaks_hz ?? []),
    ...(b?.resonance_peaks_hz ?? []),
  ];

  return (
    <>
      <h4>Impedance scan — resonance locator</h4>
      <Plot
        height={260}
        data={traces}
        layout={{
          xaxis: { title: "frequency (Hz)" },
          yaxis: { title: "|Z| (pu)", type: "log" },
          shapes: peaks.map((f) => ({
            type: "line", x0: f, x1: f, yref: "paper", y0: 0, y1: 1,
            line: { color: "#f59e0b", dash: "dot", width: 1 },
          })),
        }}
      />
      <p className="muted">
        Resonance peaks (dotted): {peaks.map((f) => Math.round(f)).join(", ") || "—"} Hz.
        The scan locates the resonance the time-domain trace exhibits.
      </p>
    </>
  );
}
