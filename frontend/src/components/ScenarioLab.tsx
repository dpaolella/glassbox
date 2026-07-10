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
  return Number.isInteger(n) ? String(n) : n.toFixed(3);
}

function ScalarRow({ label, d }: { label: string; d: DiffScalar }) {
  const up = (d.delta ?? 0) > 0;
  return (
    <tr>
      <td className="field-name">{label}</td>
      <td className="field-value">{fmt(d.a)}</td>
      <td className="field-value">{fmt(d.b)}</td>
      <td className="field-value" style={{ color: up ? "#f59e0b" : "#22c55e" }}>
        {(d.delta ?? 0) >= 0 ? "+" : ""}
        {fmt(d.delta)}
      </td>
    </tr>
  );
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
          <div className="diff-head">
            <span className="diff-a">
              A · {diff.a.spatial} · {diff.a.layer} · yrs{" "}
              {diff.a.weather_years.join(",")}
            </span>
            <span className="diff-b">
              B · {diff.b.spatial} · {diff.b.layer} · yrs{" "}
              {diff.b.weather_years.join(",")}
            </span>
          </div>

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
                <ScalarRow key={k} label={k.replace(/_/g, " ")} d={d} />
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
