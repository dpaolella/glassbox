import { useEffect, useState } from "react";
import { api, OracleMetric, OracleResult } from "../api";

// "Transparent kernel vs mature oracle" — runs the Section 11 round-trips on
// demand and shows, per engine, how closely the hand-built kernel matches the
// reference library. Validation is two-pronged: oracles here, analytical cases
// (binomial LOLP, equal-area CCT, LCL resonance) for RA and EMT.

interface CardSpec {
  key: string;
  title: string;
  kernel: string;
  oracle: string;
  run: () => Promise<OracleResult>;
}

const CARDS: CardSpec[] = [
  {
    key: "powerflow",
    title: "AC power flow (Section 6.5)",
    kernel: "hand-built Newton-Raphson",
    oracle: "pandapower",
    run: () => api.oraclePowerflow(),
  },
  {
    key: "dispatch",
    title: "Economic dispatch (Sections 6.2 / 6.3)",
    kernel: "transparent linopy core",
    oracle: "PyPSA LOPF",
    run: () => api.oracleDispatch(),
  },
  {
    key: "dynamics",
    title: "RMS swing dynamics (Section 6.6)",
    kernel: "hand-built SMIB integrator",
    oracle: "Andes",
    run: () => api.oracleDynamics(),
  },
];

function fmt(v: number | string, unit: string): string {
  if (typeof v === "string") return v;
  if (unit === "rel") return v.toExponential(2);
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  return v.toFixed(4);
}

function MetricRow({ m }: { m: OracleMetric }) {
  const pass = m.diff <= m.tol;
  return (
    <tr title={`tolerance ${m.tol}`}>
      <td className="field-name">{m.name}</td>
      <td className="field-value">{fmt(m.kernel, m.unit)}</td>
      <td className="field-value">{fmt(m.oracle, m.unit)}</td>
      <td
        className="field-value"
        style={{ color: pass ? "#22c55e" : "#f59e0b" }}
      >
        {m.diff === 0 ? "exact" : fmt(m.diff, m.unit === "rel" ? "rel" : m.unit)}
        {m.unit !== "rel" && m.diff !== 0 ? ` ${m.unit}` : ""} {pass ? "✓" : "✗"}
      </td>
    </tr>
  );
}

function OracleCard({ spec, enabled }: { spec: CardSpec; enabled: boolean }) {
  const [res, setRes] = useState<OracleResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function run() {
    setLoading(true);
    setErr(null);
    spec
      .run()
      .then(setRes)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }

  const allPass =
    res?.metrics && res.metrics.every((m) => m.diff <= m.tol);

  return (
    <div className="oracle-card">
      <div className="oracle-head">
        <div>
          <div className="oracle-title">{spec.title}</div>
          <div className="muted">
            {spec.kernel} <span style={{ color: "var(--accent)" }}>vs</span>{" "}
            {spec.oracle}
          </div>
        </div>
        {res?.available && (
          <span className={`verdict ${allPass ? "ok" : "warn"}`}>
            {allPass ? "MATCH" : "DIVERGES"}
          </span>
        )}
      </div>

      {!enabled ? (
        <p className="muted">
          {spec.oracle} is not installed. Install dev oracles with{" "}
          <code>pip install -e ".[oracles]"</code>.
        </p>
      ) : !res && !loading ? (
        <button className="run-oracle" onClick={run}>
          run round-trip
        </button>
      ) : loading ? (
        <p className="muted">running {spec.oracle}…</p>
      ) : err ? (
        <div className="error-banner">{err}</div>
      ) : res && !res.available ? (
        <p className="muted">{spec.oracle} unavailable.</p>
      ) : (
        res &&
        res.metrics && (
          <>
            <table className="field-table">
              <thead>
                <tr>
                  <th></th>
                  <th className="field-value">kernel</th>
                  <th className="field-value">oracle</th>
                  <th className="field-value">diff (tol)</th>
                </tr>
              </thead>
              <tbody>
                {res.metrics.map((m) => (
                  <MetricRow key={m.name} m={m} />
                ))}
              </tbody>
            </table>
            {res.hour !== undefined && (
              <p className="muted">snapshot hour {res.hour}</p>
            )}
            <button className="run-oracle" onClick={run}>
              re-run
            </button>
          </>
        )
      )}
    </div>
  );
}

export function OraclePanel() {
  const [avail, setAvail] = useState<Record<string, boolean> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.oracleAvailability().then(setAvail).catch((e) => setErr(String(e)));
  }, []);

  const libFor: Record<string, string> = {
    powerflow: "pandapower",
    dispatch: "pypsa",
    dynamics: "andes",
  };

  return (
    <div className="oracle-panel">
      <p className="muted">
        The transparent kernels are validated against mature libraries running
        alongside them (PRD Section 11) — the moments where they diverge are
        themselves lessons about fidelity. Resource adequacy and EMT have no
        mature oracle and rest on analytical cases (binomial LOLP, equal-area
        critical clearing time, LCL resonance) covered in the test suite.
      </p>
      {err && <div className="error-banner">{err}</div>}
      {CARDS.map((c) => (
        <OracleCard
          key={c.key}
          spec={c}
          enabled={!!avail && !!avail[libFor[c.key]]}
        />
      ))}
    </div>
  );
}
