import { useEffect, useState } from "react";
import { api, LoadScope, TimeSeriesMeta } from "../api";
import { Plot } from "./Plot";

type Mode = "load" | "series";
type Unit = "day" | "week";

// Plot aggregated load (system-wide or per region) or any raw time series, over
// a day or week window (Section 9.6). Dump the underlying array.
export function TimeSeriesPanel() {
  const [mode, setMode] = useState<Mode>("load");

  // aggregated-load controls
  const [scopes, setScopes] = useState<LoadScope[]>([]);
  const [scope, setScope] = useState<string>("all");

  // raw-series controls
  const [list, setList] = useState<TimeSeriesMeta[]>([]);
  const [id, setId] = useState<string>("");

  // shared window controls
  const [unit, setUnit] = useState<Unit>("week");
  const [count, setCount] = useState<number>(2);
  const [startDay, setStartDay] = useState<number>(0);

  const [series, setSeries] = useState<{ values: number[]; unit: string; label: string } | null>(
    null,
  );

  useEffect(() => {
    api.loadScopes().then(setScopes);
    api.timeseriesList().then((l) => {
      const plottable = l.filter((t) => t.kind !== "regime");
      setList(plottable);
      if (plottable.length) setId(plottable[0].id);
    });
  }, []);

  const startHour = Math.max(0, Math.round(startDay)) * 24;
  const lengthHours = Math.max(1, Math.round(count)) * (unit === "day" ? 24 : 168);

  useEffect(() => {
    if (mode === "load") {
      api
        .aggregatedLoad(scope, startHour, lengthHours, 1)
        .then((d) => {
          const sc = scopes.find((s) => s.id === scope);
          setSeries({
            values: d.values,
            unit: "MW",
            label: `Load · ${sc?.name ?? scope} (${d.n_loads} loads)`,
          });
        })
        .catch(() => setSeries(null));
    } else if (id) {
      api
        .timeseries(id, startHour, lengthHours, 1)
        .then((d) => setSeries({ values: d.values, unit: d.unit ?? "", label: d.id }))
        .catch(() => setSeries(null));
    }
  }, [mode, scope, id, startHour, lengthHours, scopes]);

  function dump() {
    if (!series) return;
    const blob = new Blob([JSON.stringify(series.values)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${series.label.replace(/\W+/g, "_")}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="series-panel">
      <p className="muted">
        Aggregated load by region or any raw series, over a day or week window.
      </p>

      <div className="series-controls">
        <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
          <option value="load">aggregated load</option>
          <option value="series">raw series</option>
        </select>
        {mode === "load" ? (
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            {scopes.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        ) : (
          <select value={id} onChange={(e) => setId(e.target.value)}>
            {list.map((t) => (
              <option key={t.id} value={t.id}>
                {t.id} [{t.kind}]
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="series-controls">
        <label>
          show
          <input
            type="number"
            min={1}
            max={unit === "day" ? 365 : 52}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            style={{ width: 56 }}
          />
        </label>
        <select value={unit} onChange={(e) => setUnit(e.target.value as Unit)}>
          <option value="day">day(s)</option>
          <option value="week">week(s)</option>
        </select>
        <label>
          from day
          <input
            type="number"
            min={0}
            max={3650}
            value={startDay}
            onChange={(e) => setStartDay(Number(e.target.value))}
            style={{ width: 64 }}
          />
        </label>
        <button onClick={dump}>dump array</button>
      </div>

      {series && (
        <>
          <Plot
            height={300}
            data={[
              {
                type: "scatter",
                mode: "lines",
                x: series.values.map((_, i) => startHour + i),
                y: series.values,
                line: { color: "#22c55e", width: 1 },
                name: series.label,
              },
            ]}
            layout={{
              title: { text: series.label, font: { size: 12 } },
              xaxis: { title: "hour of year" },
              yaxis: { title: series.unit },
            }}
          />
          {series.values.length > 0 && (
            <p className="muted">
              {series.values.length} hours · peak{" "}
              {Math.max(...series.values).toFixed(0)} {series.unit} · mean{" "}
              {(series.values.reduce((a, b) => a + b, 0) / series.values.length).toFixed(0)}{" "}
              {series.unit}
            </p>
          )}
        </>
      )}
    </div>
  );
}
