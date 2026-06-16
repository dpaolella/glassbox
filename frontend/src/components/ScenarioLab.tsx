import { useEffect, useState } from "react";
import {
  api,
  DiffScalar,
  ExplainPayload,
  ScenarioDiffResult,
  ScenarioPreset,
} from "../api";
import { Plot } from "./Plot";

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

export function ScenarioLab() {
  const [presets, setPresets] = useState<ScenarioPreset[]>([]);
  const [active, setActive] = useState<ScenarioPreset | null>(null);
  const [result, setResult] = useState<ScenarioDiffResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.presets().then(setPresets).catch((e) => setErr(String(e)));
  }, []);

  function run(p: ScenarioPreset) {
    setActive(p);
    setResult(null);
    setErr(null);
    setLoading(true);
    api
      .diffScenarios(p.a, p.b)
      .then(setResult)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }

  const diff = result?.diff;
  const mixKeys = diff
    ? Object.keys(diff.capacity_mix_mw).filter(
        (k) => diff.capacity_mix_mw[k].a > 0 || diff.capacity_mix_mw[k].b > 0,
      )
    : [];

  return (
    <div className="scenario-lab">
      <p className="muted">
        Each demonstration is a pair of scenarios that differ in exactly one
        operator or override (Section 10). Run one to see the diff.
      </p>
      <div className="preset-list">
        {presets.map((p) => (
          <button
            key={p.key}
            className={`preset ${active?.key === p.key ? "active" : ""}`}
            onClick={() => run(p)}
          >
            {p.name}
          </button>
        ))}
      </div>

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
                    marker: { color: "#3b82f6" },
                  },
                  {
                    type: "bar",
                    name: "B",
                    x: mixKeys,
                    y: mixKeys.map((k) => diff.capacity_mix_mw[k].b),
                    marker: { color: "#22c55e" },
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
                    marker: { color: "#3b82f6" },
                  },
                  {
                    type: "bar",
                    name: "B",
                    x: Object.keys(diff.nodal_prices),
                    y: Object.values(diff.nodal_prices).map((d) => d.b),
                    marker: { color: "#22c55e" },
                  },
                ]}
                layout={{ barmode: "group", yaxis: { title: "$/MWh" } }}
              />
            </>
          )}

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
