import { useEffect, useState } from "react";
import { api, FacetInfo, WorldSummary } from "./api";
import { NetworkCanvas } from "./components/NetworkCanvas";
import { Inspector } from "./components/Inspector";
import { OperatorPanel } from "./components/OperatorPanel";
import { WeatherPanel } from "./components/WeatherPanel";
import { TimeSeriesPanel } from "./components/TimeSeriesPanel";
import { ScenarioLab } from "./components/ScenarioLab";

export interface Selection {
  collection: string;
  id: string;
}

type Tab = "inspector" | "scenarios" | "math" | "weather" | "series";

export default function App() {
  const [summary, setSummary] = useState<WorldSummary | null>(null);
  const [facets, setFacets] = useState<FacetInfo[]>([]);
  const [layer, setLayer] = useState<string>("core");
  const [perUnit, setPerUnit] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [tab, setTab] = useState<Tab>("inspector");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.worldSummary().then(setSummary).catch((e) => setError(String(e)));
    api.facets().then(setFacets).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◧</span>
          <div>
            <h1>Glassbox</h1>
            <p className="subtitle">
              {summary ? summary.name : "loading…"} — one world, many views
            </p>
          </div>
        </div>

        <div className="layer-selector">
          <span className="layer-label">Modeling layer</span>
          <div className="chips">
            {facets.map((f) => (
              <button
                key={f.code}
                className={`chip ${layer === f.code ? "active" : ""}`}
                title={f.label}
                onClick={() => setLayer(f.code)}
              >
                {f.code}
              </button>
            ))}
          </div>
          <span className="layer-full">
            {facets.find((f) => f.code === layer)?.label}
          </span>
        </div>

        <label className="unit-toggle">
          <input
            type="checkbox"
            checked={perUnit}
            onChange={(e) => setPerUnit(e.target.checked)}
          />
          <span>{perUnit ? "per-unit" : "SI units"}</span>
        </label>
      </header>

      {error && <div className="error-banner">API error: {error}. Is the backend running on :8000?</div>}

      <div className="main">
        <div className="canvas-pane">
          <NetworkCanvas
            layer={layer}
            selection={selection}
            onSelect={(sel) => {
              setSelection(sel);
              setTab("inspector");
            }}
          />
          {summary && (
            <div className="statusbar">
              {Object.entries(summary.counts)
                .filter(([, n]) => n > 0)
                .map(([k, n]) => (
                  <span key={k} className="stat">
                    {k.replace(/_/g, " ")}: <b>{n}</b>
                  </span>
                ))}
              <span className="stat">
                weather years: <b>{summary.n_weather_years}</b>
              </span>
            </div>
          )}
        </div>

        <aside className="side-pane">
          <nav className="tabs">
            {(["inspector", "scenarios", "math", "weather", "series"] as Tab[]).map(
              (t) => (
                <button
                  key={t}
                  className={`tab ${tab === t ? "active" : ""}`}
                  onClick={() => setTab(t)}
                >
                  {t === "math" ? "operators" : t}
                </button>
              ),
            )}
          </nav>
          <div className="panel-body">
            {tab === "inspector" && (
              <Inspector selection={selection} layer={layer} perUnit={perUnit} />
            )}
            {tab === "scenarios" && <ScenarioLab />}
            {tab === "math" && <OperatorPanel layer={layer} />}
            {tab === "weather" && <WeatherPanel />}
            {tab === "series" && <TimeSeriesPanel />}
          </div>
        </aside>
      </div>
    </div>
  );
}
