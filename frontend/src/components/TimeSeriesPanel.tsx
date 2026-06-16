import { useEffect, useState } from "react";
import { api, TimeSeriesData, TimeSeriesMeta } from "../api";
import { Plot } from "./Plot";

// Plot any TimeSeries with the ability to dump the underlying array (Section 9.6).
export function TimeSeriesPanel() {
  const [list, setList] = useState<TimeSeriesMeta[]>([]);
  const [id, setId] = useState<string>("");
  const [weeks, setWeeks] = useState<number>(4);
  const [data, setData] = useState<TimeSeriesData | null>(null);

  useEffect(() => {
    api.timeseriesList().then((l) => {
      const plottable = l.filter((t) => t.kind !== "regime");
      setList(plottable);
      if (plottable.length) setId(plottable[0].id);
    });
  }, []);

  useEffect(() => {
    if (id) api.timeseries(id, 0, weeks * 168, 1).then(setData);
  }, [id, weeks]);

  function dump() {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data.values)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="series-panel">
      <p className="muted">Plot any stored time series; dump the raw array.</p>
      <div className="series-controls">
        <select value={id} onChange={(e) => setId(e.target.value)}>
          {list.map((t) => (
            <option key={t.id} value={t.id}>
              {t.id} [{t.kind}]
            </option>
          ))}
        </select>
        <label>
          weeks:
          <input
            type="number"
            min={1}
            max={52}
            value={weeks}
            onChange={(e) => setWeeks(Number(e.target.value))}
          />
        </label>
        <button onClick={dump}>dump array</button>
      </div>

      {data && (
        <Plot
          height={300}
          data={[
            {
              type: "scatter",
              mode: "lines",
              x: data.values.map((_, i) => i),
              y: data.values,
              line: { color: "#22c55e", width: 1 },
              name: data.id,
            },
          ]}
          layout={{
            xaxis: { title: "hour" },
            yaxis: { title: data.unit ?? "" },
          }}
        />
      )}
    </div>
  );
}
