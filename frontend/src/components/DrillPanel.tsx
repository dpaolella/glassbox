import { useEffect, useRef, useState } from "react";

// Control-room drill (issue #29): trip the inspected element and watch the
// system respond — the frequency trace draws itself in near-real time.

const BASE = "/api";

interface GenDrill {
  kind: "gen_trip";
  name: string;
  hour: number;
  event_mw: number;
  time_s: number[];
  frequency_hz: number[];
  nadir_hz: number;
  rocof_hz_per_s: number;
  ufls_hz: number;
  verdict: string;
  explanation: string;
}

interface LineDrill {
  kind: "line_trip";
  name: string;
  hour: number;
  violations?: { element?: string; kind?: string; value?: number; limit?: number }[];
  verdict: string;
  explanation: string;
}

type Drill = (GenDrill | LineDrill) & { verdict: string; explanation: string };

// frequency trace that reveals itself left-to-right (the "live" replay)
function FrequencyTrace({ d }: { d: GenDrill }) {
  const [frac, setFrac] = useState(0);
  const raf = useRef(0);
  useEffect(() => {
    setFrac(0);
    const t0 = performance.now();
    const dur = 2800; // ms to replay the whole trace
    const tick = (t: number) => {
      const f = Math.min(1, (t - t0) / dur);
      setFrac(f);
      if (f < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [d]);

  const W = 380;
  const H = 120;
  const fs = d.frequency_hz;
  if (!fs.length) return null;
  const fmax = Math.max(...fs) + 0.05;
  const fmin = Math.min(Math.min(...fs), d.ufls_hz) - 0.1;
  const sy = (f: number) => H - ((f - fmin) / (fmax - fmin)) * H;
  const n = Math.max(2, Math.floor(fs.length * frac));
  const pts = fs.slice(0, n)
    .map((f, i) => `${(i / (fs.length - 1)) * W},${sy(f)}`)
    .join(" ");
  const nadirIdx = fs.indexOf(Math.min(...fs));
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H + 16}`} className="drill-trace">
      {/* UFLS threshold */}
      <line x1={0} x2={W} y1={sy(d.ufls_hz)} y2={sy(d.ufls_hz)}
        stroke="var(--bad)" strokeDasharray="5 4" strokeWidth={1} />
      <text x={W - 2} y={sy(d.ufls_hz) - 3} textAnchor="end" fontSize={9}
        fill="var(--bad)">
        UFLS {d.ufls_hz} Hz — load shedding
      </text>
      {/* nominal */}
      <line x1={0} x2={W} y1={sy(fs[0])} y2={sy(fs[0])}
        stroke="var(--muted)" strokeDasharray="2 4" strokeWidth={0.7} />
      <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth={2} />
      {frac >= nadirIdx / fs.length && (
        <>
          <circle cx={(nadirIdx / (fs.length - 1)) * W} cy={sy(d.nadir_hz)}
            r={3.5} fill="var(--warn)" />
          <text x={(nadirIdx / (fs.length - 1)) * W + 6} y={sy(d.nadir_hz) + 4}
            fontSize={10} fill="var(--warn)">
            nadir {d.nadir_hz} Hz
          </text>
        </>
      )}
      <text x={2} y={H + 12} fontSize={9} fill="var(--muted)">
        seconds after the trip →
      </text>
    </svg>
  );
}

export function DrillPanel({
  collection,
  id,
}: {
  collection: string;
  id: string;
}) {
  const [drill, setDrill] = useState<Drill | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setDrill(null);
    setErr(null);
  }, [collection, id]);

  if (!["generators", "ac_lines"].includes(collection)) return null;

  function run() {
    setLoading(true);
    setErr(null);
    fetch(`${BASE}/drill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ collection, id }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`drill -> ${r.status}`);
        return r.json();
      })
      .then(setDrill)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }

  const verdictColor = (v: string) =>
    v === "survived" || v === "secure" ? "var(--good)"
    : v === "no-op" || v === "not studied" ? "var(--muted)"
    : "var(--bad)";

  return (
    <div className="drill-panel">
      {!drill && !loading && (
        <button className="preset" onClick={run}
          title={collection === "generators"
            ? "Trip this unit at the peak operating point and watch the system frequency respond (inertia, governors, FFR) — the control-room drill."
            : "Take this line out at the peak operating point and check the N-1 AC power flow: do the survivors stay within thermal and voltage limits?"}>
          ⚡ trip it (control-room drill)
        </button>
      )}
      {loading && <p className="muted">tripping {id} — solving the response…</p>}
      {err && <div className="error-banner">{err}</div>}
      {drill && (
        <>
          <div className="drill-verdict" style={{ borderColor: verdictColor(drill.verdict) }}>
            <span style={{ color: verdictColor(drill.verdict), fontWeight: 700,
                           textTransform: "uppercase" }}>
              {drill.verdict}
            </span>
            {drill.kind === "gen_trip" && (drill as GenDrill).event_mw > 0 && (
              <span className="muted"> · −{(drill as GenDrill).event_mw} MW ·
                RoCoF {(drill as GenDrill).rocof_hz_per_s} Hz/s</span>
            )}
          </div>
          {drill.kind === "gen_trip" && (drill as GenDrill).frequency_hz?.length > 0 && (
            <FrequencyTrace d={drill as GenDrill} />
          )}
          {drill.kind === "line_trip" && (drill as LineDrill).violations &&
            (drill as LineDrill).violations!.length > 0 && (
            <ul className="drill-violations">
              {(drill as LineDrill).violations!.slice(0, 6).map((v, i) => (
                <li key={i}>
                  {String(v.element ?? v.kind ?? "limit")}: {JSON.stringify(v)}
                </li>
              ))}
            </ul>
          )}
          <p className="muted" style={{ fontSize: "11px" }}>{drill.explanation}</p>
          <button className="preset" onClick={run}>↻ trip it again</button>
        </>
      )}
    </div>
  );
}
