import { useEffect, useRef, useState } from "react";
import { api, FacetInfo, MapResults, WorldSummary } from "./api";
import { NetworkCanvas } from "./components/NetworkCanvas";
import { Inspector } from "./components/Inspector";
import { OperatorPanel } from "./components/OperatorPanel";
import { WeatherPanel } from "./components/WeatherPanel";
import { TimeSeriesPanel } from "./components/TimeSeriesPanel";
import { ScenarioLab } from "./components/ScenarioLab";
import { OraclePanel } from "./components/OraclePanel";
import { Catalog } from "./components/Catalog";
import { GlossaryPanel } from "./components/GlossaryPanel";
import { Tour, TourStep, tourDone } from "./components/Tour";

export interface Selection {
  collection: string;
  id: string;
}

type Tab =
  | "inspector"
  | "catalog"
  | "scenarios"
  | "math"
  | "oracles"
  | "glossary"
  | "weather"
  | "series";

// World collections that map to a Catalog group (for clickable status-bar counts)
const CATALOG_COLLECTIONS = new Set([
  "buses", "zones", "ac_lines", "transformers", "dc_lines", "shunts",
  "interfaces", "generators", "hydro_units", "storage_units", "loads",
  "expansion_candidates", "resource_potentials",
  "fuels", "cost_curves", "policies", "reserve_products",
  "system_constraints", "disturbances",
]);

export default function App() {
  const [summary, setSummary] = useState<WorldSummary | null>(null);
  const [facets, setFacets] = useState<FacetInfo[]>([]);
  const [layer, setLayer] = useState<string>("core");
  const [perUnit, setPerUnit] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [tab, setTab] = useState<Tab>("inspector");
  const [catalogFocus, setCatalogFocus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // solved-run results painted on the map (pushed by the Scenario Lab)
  const [mapResults, setMapResults] = useState<MapResults | null>(null);
  // light "projector mode" for classrooms (persisted; tokens flip via data-theme)
  const [theme, setTheme] = useState<string>(
    () => localStorage.getItem("gb-theme") ?? "dark",
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("gb-theme", theme);
  }, [theme]);
  // first-run guided tour (issue #21)
  const [showTour, setShowTour] = useState(() => !tourDone());
  const tourSteps: TourStep[] = [
    {
      title: "One world, many views",
      body: "Glassbox stores exactly one fine-grained power system — the map. " +
        "Every modeling layer (capacity expansion, production cost, adequacy, " +
        "power flow, dynamics, EMT) is a projection of this one world, and every " +
        "projection is inspectable.",
    },
    {
      title: "The modeling-layer chips",
      body: "The chips in the header (CORE, INV, OPS, …) switch the abstraction " +
        "level. The same object reveals different fields at each layer: a " +
        "generator shows heat rate to operations, inertia to dynamics, and " +
        "build options to capacity expansion.",
      action: () => setLayer("inv"),
    },
    {
      title: "Click anything to inspect it",
      body: "Every bus, line, candidate, and supply curve on the map opens in " +
        "the Inspector, filtered to the fields the current layer consumes — " +
        "with units, per-unit bases, and hover definitions on every row.",
      action: () => setTab("inspector"),
    },
    {
      title: "Run an experiment",
      body: "The Scenarios tab runs A/B pairs that differ in exactly one " +
        "modeling choice — nodal vs zonal, one weather year vs many, carbon " +
        "price vs none. The results paint onto the map, and a callout explains " +
        "what the numbers mean.",
      action: () => setTab("scenarios"),
    },
    {
      title: "Don't trust us — check the oracles",
      body: "The Oracles tab round-trips the same world through independent " +
        "reference implementations (pandapower, PyPSA, Andes) and compares " +
        "results metric by metric. The Glossary tab defines every term. Enjoy!",
      action: () => setTab("oracles"),
    },
  ];

  // resizable side panel (persisted)
  const [panelWidth, setPanelWidth] = useState<number>(() => {
    const saved = Number(localStorage.getItem("glassbox.panelWidth"));
    return saved >= 320 && saved <= 760 ? saved : 440;
  });

  useEffect(() => {
    api.worldSummary().then(setSummary).catch((e) => setError(String(e)));
    api.facets().then(setFacets).catch((e) => setError(String(e)));
  }, []);

  function startResize(e: React.MouseEvent) {
    e.preventDefault();
    const move = (ev: MouseEvent) => {
      const w = Math.min(760, Math.max(320, window.innerWidth - ev.clientX));
      setPanelWidth(w);
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      document.body.style.cursor = "";
      localStorage.setItem("glassbox.panelWidth", String(panelWidthRef.current));
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    document.body.style.cursor = "col-resize";
  }

  const panelWidthRef = useRef(panelWidth);
  panelWidthRef.current = panelWidth;

  const activeFacet = facets.find((f) => f.code === layer);

  return (
    <div className="app">
      {showTour && <Tour steps={tourSteps} onClose={() => setShowTour(false)} />}
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
                title={`${f.label} — ${f.description}`}
                onClick={() => setLayer(f.code)}
              >
                {f.code}
              </button>
            ))}
          </div>
        </div>

        <label className="unit-toggle">
          <input
            type="checkbox"
            checked={perUnit}
            onChange={(e) => setPerUnit(e.target.checked)}
          />
          <span title="Applies to the Inspector's field values">{perUnit ? "per-unit (inspector)" : "SI units (inspector)"}</span>
        </label>
        <button
          className="theme-toggle"
          title="Replay the guided tour"
          onClick={() => setShowTour(true)}
        >
          ? tour
        </button>
        <button
          className="theme-toggle"
          title="Toggle light 'projector mode' (high contrast for classrooms)"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        >
          {theme === "dark" ? "☀ projector" : "◐ dark"}
        </button>
      </header>

      <div className="context-bar">
        <span className="ctx-layer">{activeFacet?.label ?? layer}</span>
        <span className="ctx-desc">{activeFacet?.description}</span>
      </div>

      {error && <div className="error-banner">API error: {error}. Is the backend running on :8000?</div>}

      <div className="main">
        <div className="canvas-pane">
          <NetworkCanvas
            layer={layer}
            selection={selection}
            results={mapResults}
            onClearResults={() => setMapResults(null)}
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
                  <button
                    key={k}
                    className="stat"
                    title="browse in the Catalog"
                    onClick={() => {
                      if (CATALOG_COLLECTIONS.has(k)) {
                        setCatalogFocus(k);
                        setTab("catalog");
                      }
                    }}
                  >
                    {k.replace(/_/g, " ")}: <b>{n}</b>
                  </button>
                ))}
              <button
                className="stat"
                title="open the Weather tab"
                onClick={() => setTab("weather")}
              >
                weather years: <b>{summary.n_weather_years}</b>
              </button>
            </div>
          )}
        </div>

        <div
          className="resizer"
          onMouseDown={startResize}
          title="Drag to resize the panel"
        />

        <aside className="side-pane" style={{ width: panelWidth }}>
          <nav className="tabs">
            {(["inspector", "catalog", "scenarios", "math", "oracles", "glossary", "weather", "series"] as Tab[]).map(
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
              <Inspector
                selection={selection}
                layer={layer}
                perUnit={perUnit}
                onSelect={setSelection}
              />
            )}
            {tab === "catalog" && (
              <Catalog
                onSelect={setSelection}
                onOpenTab={(t) => setTab(t as Tab)}
                focus={catalogFocus}
              />
            )}
            {tab === "scenarios" && (
              <ScenarioLab
                layerLabel={activeFacet?.label ?? layer}
                layerEngine={activeFacet?.engine ?? null}
                onPickLayer={setLayer}
                onMapResults={setMapResults}
                mapResults={mapResults}
              />
            )}
            {tab === "math" && <OperatorPanel layer={layer} />}
            {tab === "glossary" && <GlossaryPanel />}
            {tab === "oracles" && <OraclePanel />}
            {tab === "weather" && <WeatherPanel />}
            {tab === "series" && <TimeSeriesPanel />}
          </div>
        </aside>
      </div>
    </div>
  );
}
