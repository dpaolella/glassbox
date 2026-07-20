import { useEffect, useRef, useState } from "react";
import { api, OpsState, OpsReport } from "../api";

/** The Control Room tab (issue #56 Phase 1): a compact EMS.
 *
 * Server holds the session; this panel polls /api/opsim/state at 1 Hz and
 * renders the operator's always-visible quantities: frequency, ACE, load vs
 * generation, reserves, line loadings (rho), alarms, basepoints, the event
 * log, and the action bar. Every rejected action shows its reason — the
 * legality explanation IS the curriculum.
 */

function Spark({ data, color, refLine, height = 34 }: {
  data: number[]; color: string; refLine?: number; height?: number;
}) {
  if (!data.length) return <svg height={height} width="100%" />;
  const w = 220, h = height, n = data.length;
  const lo = Math.min(...data, refLine ?? Infinity);
  const hi = Math.max(...data, refLine ?? -Infinity);
  const span = hi - lo || 1;
  const pt = (v: number, i: number) =>
    `${(i / Math.max(n - 1, 1)) * w},${h - 3 - ((v - lo) / span) * (h - 6)}`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
         style={{ width: "100%", height: h }}>
      {refLine !== undefined && (
        <line x1="0" x2={w} y1={h - 3 - ((refLine - lo) / span) * (h - 6)}
              y2={h - 3 - ((refLine - lo) / span) * (h - 6)}
              stroke="var(--muted, #888)" strokeDasharray="3 3" strokeWidth="0.7" />
      )}
      <polyline points={data.map(pt).join(" ")} fill="none"
                stroke={color} strokeWidth="1.6" />
    </svg>
  );
}

function rhoColor(rho: number): string {
  if (rho >= 1.0) return "var(--bad, #e05555)";
  if (rho >= 0.9) return "var(--warn, #e0a935)";
  return "var(--ok, #4caf7d)";
}

export function ControlRoomPanel() {
  const [state, setState] = useState<OpsState | null>(null);
  const [report, setReport] = useState<OpsReport | null>(null);
  const [subs, setSubs] = useState<Record<string, any>[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [study, setStudy] = useState<Record<string, any> | null>(null);
  const [running, setRunning] = useState(false);
  const [redisGen, setRedisGen] = useState("");
  const [redisMw, setRedisMw] = useState(10);
  const [switchId, setSwitchId] = useState("");
  const [scenarios, setScenarios] = useState<{ id: string; name: string; lesson: string; pass: string }[]>([]);
  const [scenario, setScenario] = useState("");
  const timer = useRef<number | null>(null);

  const poll = async () => {
    try {
      const st = await api.opsState();
      setState(st);
      if (st.clock.finished && !report) setReport(await api.opsReport());
    } catch {
      setRunning(false);            // 409: no session yet
    }
  };

  useEffect(() => {
    if (!running) return;
    poll();
    timer.current = window.setInterval(poll, 1000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  // adopt a session that already exists server-side (tab switches, reloads)
  useEffect(() => {
    api.opsState().then((st) => { setState(st); setRunning(true); })
      .catch(() => {});
    api.opsScenarios().then(setScenarios).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = async () => {
    setReport(null); setMsg(null); setStudy(null);
    const st = await api.opsStart(
      scenario ? { scenario, speed: 60 }
               : { seed: 42, n_steps: 144, speed: 60 });
    setState(st);
    setSubs(await api.opsSubstations());
    setRunning(true);
  };

  const act = async (body: Record<string, unknown>) => {
    const r = await api.opsAction(body);
    setMsg(r.applied ? (r.note ?? "✓ applied") : `✗ ${r.reason}`);
    poll();
  };

  const doStudy = async (body: Record<string, unknown>) => {
    setStudy(await api.opsStudy(body));
  };

  if (!state) {
    return (
      <div className="control-room">
        <h3>Control Room</h3>
        <p>
          Take the desk for one operating shift (05:30 → 17:30) on the system
          planning built. You are the Balancing Authority and Transmission
          Operator: hold frequency, respect line ratings, and keep the lights
          on while forecast error, forced outages, and protection do their
          worst. The turnover briefing arrives when you start.
        </p>
        <select value={scenario} onChange={(e) => setScenario(e.target.value)}
                title={scenarios.find((s) => s.id === scenario)?.lesson}>
          <option value="">default day (seed 42)</option>
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>{" "}
        <button className="primary" onClick={start}>Start shift</button>
        {scenario && (
          <p className="cr-lesson">
            {scenarios.find((s) => s.id === scenario)?.lesson}
            <br /><em>pass: {scenarios.find((s) => s.id === scenario)?.pass}</em>
          </p>
        )}
      </div>
    );
  }

  const { clock, traces, alarms, lines } = state;
  const last = (a: number[]) => (a.length ? a[a.length - 1] : 0);
  const freq = last(traces.freq_hz) || 60;
  const ace = last(traces.ace_mw);
  const load = last(traces.load_mw);
  const gen = last(traces.gen_mw);
  const unserved = last(traces.unserved_mw);
  const lam = last(traces.lambda_per_mwh);
  const gens = Object.keys(state.basepoints);
  const switches = subs.flatMap((s) => s.switches ?? []);

  return (
    <div className="control-room">
      {/* clock + controls */}
      <div className="cr-clock-row">
        <strong className="cr-time" title="simulation clock">
          ⏱ {clock.sim_time}
        </strong>
        <span className="cr-progress">
          step {clock.step}/{clock.n_steps}
        </span>
        {[0, 10, 60, 240].map((sp) => (
          <button key={sp}
                  className={`tab ${clock.speed === sp ? "active" : ""}`}
                  title={sp === 0 ? "freeze the clock" : `${sp}x real time`}
                  onClick={async () => { await api.opsClock(sp); poll(); }}>
            {sp === 0 ? "❚❚" : `${sp}×`}
          </button>
        ))}
        <button className="tab" onClick={start} title="new seeded shift">
          restart
        </button>
        {clock.finished && <strong className="cr-done">shift complete</strong>}
      </div>

      {state.in_blackout && (
        <div className="cr-eea" title="Served load has collapsed. Restoration (EOP-005): re-commit units, reclose cleared lines, pick load back up in blocks.">
          ⚫ BLACKOUT — restore load in blocks
        </div>
      )}
      {(state.voltage_violations ?? []).length > 0 && (
        <div className="cr-sol" title="VAR-001: buses outside their voltage schedule. Reactive dispatch (adjust generator AVR setpoints) to correct.">
          ⚡ voltage: {(state.voltage_violations ?? [])
            .map((v) => v.bus).join(", ")} off-schedule
        </div>
      )}
      {(state.eea_level ?? 0) > 0 && (
        <div className="cr-eea" title="Energy Emergency Alert, declared by the (simulated) Reliability Coordinator per EOP-011">
          ⚠ EEA-{state.eea_level} in effect
        </div>
      )}
      {state.hruc_pending && (
        <div className="cr-sol" title="Hourly reliability check: the next hour looks short of load + largest-unit reserve. Commitment is a decision, not an outcome.">
          🔌 HRUC: commit {state.hruc_pending.unit}?
          ({state.hruc_pending.short_mw} MW short){" "}
          <button onClick={() => act({ type: "approve_hruc" })}>approve</button>
          <button onClick={() => act({ type: "deny_hruc" })}>deny</button>
        </div>
      )}
      {Object.entries(state.sol_clocks ?? {}).map(([lid, min]) => (
        <div key={lid} className="cr-sol" title="TOP-001: post-contingency SOL exceedances must be mitigated within 30 minutes">
          ⏳ SOL clock: {lid} — {min} of 30 min
        </div>
      ))}

      {/* report card */}
      {report && (
        <div className="cr-report">
          <h4>Shift report card</h4>
          {report.scenario_pass && (
            <div className={`cr-scenario-verdict ${report.scenario_pass.passed ? "pass" : "fail"}`}>
              {report.scenario_pass.passed ? "✓ PASSED" : "✗ did not pass"}
              {" — "}{report.scenario_pass.criterion}
            </div>
          )}
          {report.nerc?.cps1_pct !== undefined && (
            <div className="cr-report-totals">
              CPS1 {report.nerc.cps1_pct}% · BAAL violations{" "}
              {report.nerc.baal?.violations} · DCS{" "}
              {report.nerc.dcs?.all_recovered ? "recovered" : "FAILED"}
            </div>
          )}
          {Object.entries(report.grades).map(([k, g]) => (
            <span key={k} className={`cr-grade cr-grade-${g}`}
                  title={report.note}>
              {k.replace("_", " ")}: <strong>{g}</strong>
            </span>
          ))}
          <div className="cr-report-totals">
            cost ${report.totals.energy_cost?.toLocaleString()} ·
            unserved {report.totals.unserved_mwh} MWh ·
            trips {report.totals.line_trips} ·
            max Δf {report.totals.max_freq_dev_hz} Hz
          </div>
        </div>
      )}

      {/* the four always-on strips */}
      <div className="cr-strips">
        <div className="cr-strip" title="System frequency vs 60.000 Hz. The IMAGINARY lesson: this must feel alive — it sags when the area under-generates.">
          <div className="cr-strip-head">
            <span>frequency</span>
            <strong style={{ color: Math.abs(freq - 60) > 0.05 ? "var(--bad, #e05555)" : undefined }}>
              {freq.toFixed(3)} Hz
            </strong>
          </div>
          <Spark data={traces.freq_hz.slice(-72)} color="var(--accent, #58a6ff)" refLine={60} />
        </div>
        <div className="cr-strip" title="Area Control Error (islanded form): generation minus load. Negative = under-generating. AGC chases this toward zero.">
          <div className="cr-strip-head">
            <span>ACE</span><strong>{ace.toFixed(1)} MW</strong>
          </div>
          <Spark data={traces.ace_mw.slice(-72)} color="var(--warn, #e0a935)" refLine={0} />
        </div>
        <div className="cr-strip" title="Served load (actual) vs total generation.">
          <div className="cr-strip-head">
            <span>load / gen</span>
            <strong>{load.toFixed(0)} / {gen.toFixed(0)} MW</strong>
          </div>
          <Spark data={traces.load_mw.slice(-72)} color="var(--muted, #999)" />
        </div>
        <div className="cr-strip" title="System lambda: marginal cost of the marginal dispatched unit each interval.">
          <div className="cr-strip-head">
            <span>λ</span><strong>${lam.toFixed(0)}/MWh</strong>
          </div>
          <Spark data={traces.lambda_per_mwh.slice(-72)} color="var(--build, #d29922)" />
        </div>
      </div>

      {/* status tiles */}
      <div className="cr-tiles">
        <span title="regulation headroom on reserve-eligible online units">
          reg headroom <strong>{state.regulation_headroom_mw} MW</strong>
        </span>
        <span title="load currently unserved">
          unserved <strong style={{ color: unserved > 0 ? "var(--bad, #e05555)" : undefined }}>
            {unserved.toFixed(1)} MW
          </strong>
        </span>
        <span title="units on forced outage">
          units out <strong>{state.out_generators.length ? state.out_generators.join(", ") : "none"}</strong>
        </span>
        {state.se && (
          <span title={`State estimator: ${state.se.redundancy} measurements per state; bad data identified: ${state.se.bad_points.join(", ") || "none"}. Operations never sees truth — only this estimate.`}>
            SE <strong style={{ color: state.se.health === "good" ? undefined
              : state.se.health === "degraded" ? "var(--warn, #e0a935)"
              : "var(--bad, #e05555)" }}>{state.se.health}</strong>
          </span>
        )}
        {state.nodal_lmps && Object.keys(state.nodal_lmps).length > 0 && (
          <span title="Nodal LMPs from the real-time LP's balance duals — congestion and scarcity separate prices by location.">
            LMP spread <strong>
              ${Math.min(...Object.values(state.nodal_lmps)).toFixed(0)}–
              {Math.max(...Object.values(state.nodal_lmps)).toFixed(0)}
            </strong>
          </span>
        )}
        {(traces.ni_sched_mw?.some((v) => v !== 0) ||
          traces.ni_actual_mw?.some((v) => v !== 0)) && (
          <span title="Net interchange: actual vs scheduled (exports positive). The gap is inadvertent interchange — leaning on the neighbors.">
            NI <strong>{last(traces.ni_actual_mw).toFixed(0)}
            /{last(traces.ni_sched_mw).toFixed(0)} MW</strong>
          </span>
        )}
        {state.manual_shed_mw > 0 && (
          <span>shed <strong style={{ color: "var(--bad, #e05555)" }}>
            {state.manual_shed_mw} MW</strong></span>
        )}
      </div>

      {/* lines (rho) */}
      <details open>
        <summary title="Per-line loading vs emergency rating — Grid2Op's rho, the hero metric. Above 1.0 for 2 steps and protection trips the line.">
          <strong>Line loadings (ρ vs emergency)</strong>
        </summary>
        <table className="cr-table">
          <tbody>
            {lines.slice(0, 8).map((l) => (
              <tr key={l.id} className={l.tripped ? "cr-tripped" : ""}>
                <td>{l.id}</td>
                <td>{l.tripped ? "TRIPPED" : `${l.flow_mw} MW`}</td>
                <td style={{ width: "45%" }}>
                  <div className="cr-rho-track">
                    <div className="cr-rho-bar" style={{
                      width: `${Math.min(l.rho_emergency, 1.3) / 1.3 * 100}%`,
                      background: rhoColor(l.rho_emergency),
                    }} />
                  </div>
                </td>
                <td>{(l.rho_emergency * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      {/* alarms */}
      <details open={state.unacked_critical > 0}>
        <summary>
          <strong>Alarms</strong>{" "}
          {state.unacked_critical > 0 && (
            <span className="cr-alarm-badge">{state.unacked_critical} unacked</span>
          )}
        </summary>
        <ul className="cr-alarms">
          {alarms.slice(-10).reverse().map((a) => (
            <li key={a.id} className={`cr-alarm cr-${a.severity} ${a.acked ? "cr-acked" : ""}`}>
              <span>[{a.step}] {a.text}</span>
              {!a.acked && (
                <button onClick={() => act({ type: "ack_alarm", id: a.id })}>
                  ack
                </button>
              )}
            </li>
          ))}
        </ul>
      </details>

      {/* action bar */}
      <div className="cr-actions">
        <h4>Operator actions</h4>
        <div className="cr-action-row">
          <select value={redisGen} onChange={(e) => setRedisGen(e.target.value)}>
            <option value="">redispatch unit…</option>
            {gens.map((g) => (
              <option key={g} value={g}>{g} ({state.basepoints[g]} MW)</option>
            ))}
          </select>
          <input type="number" value={redisMw} style={{ width: 70 }}
                 onChange={(e) => setRedisMw(Number(e.target.value))} /> MW
          <button disabled={!redisGen}
                  onClick={() => act({ type: "redispatch", id: redisGen, delta_mw: redisMw })}>
            apply
          </button>
          <button disabled={!redisGen} title="what-if against the current interval — never commits (the obs.simulate lesson)"
                  onClick={() => doStudy({ type: "redispatch", id: redisGen, delta_mw: redisMw })}>
            study
          </button>
          <button disabled={!redisGen} title="reactive dispatch: raise this unit's AVR voltage setpoint by 0.02 pu (VAR-001)"
                  onClick={() => act({ type: "voltage", id: redisGen, delta_pu: 0.02 })}>
            V+
          </button>
          <button disabled={!redisGen} title="lower this unit's AVR voltage setpoint by 0.02 pu"
                  onClick={() => act({ type: "voltage", id: redisGen, delta_pu: -0.02 })}>
            V−
          </button>
        </div>
        <div className="cr-action-row">
          <select value={switchId} onChange={(e) => setSwitchId(e.target.value)}>
            <option value="">operate switch…</option>
            {switches.map((s: any) => (
              <option key={s.id} value={s.id}>
                {s.id} [{s.kind}] {s.open ? "(open)" : "(closed)"}
              </option>
            ))}
          </select>
          <button disabled={!switchId}
                  onClick={() => act({ type: "switch", id: switchId, open: true })}>
            open
          </button>
          <button disabled={!switchId}
                  onClick={() => act({ type: "switch", id: switchId, open: false })}>
            close
          </button>
          <button disabled={!switchId} title="what-if"
                  onClick={() => doStudy({ type: "switch", id: switchId, open: true })}>
            study open
          </button>
        </div>
        <div className="cr-action-row">
          <button onClick={() => act({ type: "shed_load", mw: 25 })}
                  title="EEA-3 last resort: interrupt 25 MW of firm load. Scored — but scored better than letting protection do it.">
            shed 25 MW
          </button>
          <button onClick={() => act({ type: "shed_load", mw: -25 })}
                  disabled={state.manual_shed_mw <= 0}>
            restore 25 MW
          </button>
          <button onClick={() => act({ type: "request_sced" })}
                  title="ask the market to re-solve now (it re-solves hourly, and promptly after contingencies)">
            re-run SCED
          </button>
        </div>
        {msg && <div className="cr-msg">{msg}</div>}
        {study && study.ok && (
          <div className="cr-study">
            <strong>study — {study.study}:</strong>{" "}
            {study.would_overload?.length
              ? `⚠ would overload ${study.would_overload.map((w: any) => w.id).join(", ")}`
              : "no overloads"}{" "}
            (worst: {study.worst_lines?.[0]?.id} at{" "}
            {((study.worst_lines?.[0]?.rho_emergency ?? 0) * 100).toFixed(0)}%)
            <button onClick={() => setStudy(null)}>×</button>
          </div>
        )}
      </div>

      {/* event log */}
      <details>
        <summary><strong>Event log</strong></summary>
        <ul className="cr-events">
          {state.events.slice(-15).reverse().map((e, i) => (
            <li key={i}>
              [{e.step === -1 ? "briefing" : e.step}] {e.kind}
              {e.id ? ` — ${e.id}` : ""}
              {e.reason ? ` (${e.reason})` : e.detail ? ` (${e.detail})` : ""}
              {e.sfr ? ` · nadir ${e.sfr.nadir_hz.toFixed(3)} Hz` : ""}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
