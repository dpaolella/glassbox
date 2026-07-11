import { useState } from "react";
import { techColor } from "../theme";
import { Plot } from "./Plot";

// Multi-year planning study UI (issue #33): run the myopic rolling expansion
// and watch the fleet transform stage by stage.

const BASE = "/api";

interface Stage {
  year: number;
  demand_scale: number;
  retired: { id: string; name: string; p_max_mw: number }[];
  built_capacity_mw: Record<string, number>;
  built_storage_power_mw: Record<string, number>;
  built_transmission_mw: Record<string, number>;
  built_resource_potential_mw: Record<string, number>;
  capacity_mix_mw: Record<string, number>;
  total_cost: number;
  vre_penetration?: number;
  curtailment_mwh_weighted?: number;
  unserved_mwh_weighted?: number;
}

interface Study {
  start_year: number;
  growth_per_year: number;
  stages: Stage[];
  note: string;
}

export function PlanningStudy() {
  const [study, setStudy] = useState<Study | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function run() {
    setLoading(true);
    setErr(null);
    fetch(`${BASE}/scenario/planning_study`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`planning study -> ${r.status}`);
        return r.json();
      })
      .then(setStudy)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }

  const techs = study
    ? Array.from(
        new Set(study.stages.flatMap((s) => Object.keys(s.capacity_mix_mw))),
      ).filter((t) => study.stages.some((s) => (s.capacity_mix_mw[t] ?? 0) > 1))
    : [];

  return (
    <div>
      <div className="catalog-section" title="A myopic rolling capacity expansion: each stage the load grows, aged plants retire, the CEM decides what to build for that stage, and its builds are committed into the world the next stage inherits (the build->operate pipeline). Path dependency is part of the lesson.">
        planning study (2026 → 2038)
      </div>
      {!study && !loading && (
        <button className="preset" onClick={run}
          title="4 stages x 4 years, 2%/yr load growth, retirements honored, builds committed between stages">
          ▶ run 12-year expansion study
        </button>
      )}
      {loading && (
        <p className="muted">
          running 4 chained expansion stages (grow → retire → build → commit)…
        </p>
      )}
      {err && <div className="error-banner">{err}</div>}
      {study && (
        <>
          <Plot
            height={230}
            data={techs.map((t) => ({
              x: study.stages.map((s) => String(s.year)),
              y: study.stages.map((s) => s.capacity_mix_mw[t] ?? 0),
              name: t,
              type: "bar" as const,
              marker: { color: techColor(t) },
            }))}
            layout={{ barmode: "stack", yaxis: { title: "installed MW" } }}
          />
          <table className="field-table">
            <thead>
              <tr>
                <th>stage</th>
                <th className="field-value">load ×</th>
                <th className="field-value">built MW</th>
                <th className="field-value">VRE share</th>
              </tr>
            </thead>
            <tbody>
              {study.stages.map((s) => {
                const builds = {
                  ...s.built_capacity_mw,
                  ...s.built_storage_power_mw,
                  ...s.built_transmission_mw,
                  ...s.built_resource_potential_mw,
                };
                const totalBuilt = Object.values(builds).reduce((a, b) => a + b, 0);
                const detail = [
                  ...Object.entries(builds).map(
                    ([k, v]) => `+${Math.round(v)} MW ${k.replace(/^(rp_|cand_|user_)/, "")}`,
                  ),
                  ...s.retired.map((r) => `retired ${r.name} (−${Math.round(r.p_max_mw)} MW)`),
                ].join("\n");
                return (
                  <tr key={s.year} title={detail || "no builds or retirements"}>
                    <td className="field-name has-tip">
                      {s.year}
                      {s.retired.length > 0 && " ⚰"}
                    </td>
                    <td className="field-value">{s.demand_scale.toFixed(2)}</td>
                    <td className="field-value">{Math.round(totalBuilt).toLocaleString()}</td>
                    <td className="field-value">
                      {s.vre_penetration != null ? `${(s.vre_penetration * 100).toFixed(0)}%` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted" style={{ fontSize: "11px" }}>{study.note}</p>
          <button className="preset" onClick={run}>↻ re-run</button>
        </>
      )}
    </div>
  );
}
