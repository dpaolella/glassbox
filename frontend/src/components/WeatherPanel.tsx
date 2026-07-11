import { useEffect, useState } from "react";
import { api, GroundTruth } from "../api";
import { Plot } from "./Plot";

// The multi-weather-year lesson (Section 7): show "the truth" (many years) next
// to "what one year implies" (the per-year spread).
export function WeatherPanel() {
  const [sites, setSites] = useState<{ id: string; kind: string; name: string }[]>([]);
  const [siteId, setSiteId] = useState<string>("");
  const [gt, setGt] = useState<GroundTruth | null>(null);

  useEffect(() => {
    api.weatherSites().then((s) => {
      const vre = s.filter((x) => x.kind === "wind" || x.kind === "solar");
      setSites(vre);
      if (vre.length) setSiteId(vre[0].id);
    });
  }, []);

  useEffect(() => {
    if (!siteId) return;
    setGt(null); // clear stale charts while the new site loads
    api.groundTruth(siteId, "availability").then(setGt).catch(() => setGt(null));
  }, [siteId]);

  return (
    <div className="weather-panel">
      <p className="muted">
        The generator defines the ground truth, so we can show exactly how badly
        a single sampled year misrepresents it (Section 2.4).
      </p>
      <select value={siteId} onChange={(e) => setSiteId(e.target.value)}>
        {sites.map((s) => (
          <option key={s.id} value={s.id}>
            {s.id} ({s.kind})
          </option>
        ))}
      </select>

      {gt && (
        <>
          <h4>True availability distribution ({gt.n_years} years)</h4>
          <Plot
            height={220}
            data={[
              {
                type: "bar",
                x: gt.truth.bin_edges.slice(0, -1),
                y: gt.truth.density,
                marker: { color: "#22c55e" },
                name: "all years",
              },
            ]}
            layout={{
              xaxis: { title: "availability (pu)" },
              yaxis: { title: "density" },
              shapes: [
                {
                  type: "line",
                  x0: gt.truth.mean,
                  x1: gt.truth.mean,
                  y0: 0,
                  y1: Math.max(...gt.truth.density),
                  line: { color: "#e6edf6", dash: "dash" },
                },
              ],
            }}
          />

          <h4>Capacity factor by sampled year</h4>
          <Plot
            height={220}
            data={[
              {
                type: "bar",
                x: gt.per_year_means.map((_, i) => `yr ${i + 1}`),
                y: gt.per_year_means,
                marker: { color: "#3b82f6" },
                name: "per-year mean",
              },
              {
                type: "scatter",
                mode: "lines",
                x: gt.per_year_means.map((_, i) => `yr ${i + 1}`),
                y: gt.per_year_means.map(() => gt.truth.mean),
                line: { color: "#e6edf6", dash: "dash" },
                name: "true mean",
              },
            ]}
            layout={{ yaxis: { title: "mean availability (pu)" } }}
          />
          <p className="muted">
            Spread across years ={" "}
            {(
              Math.max(...gt.per_year_means) - Math.min(...gt.per_year_means)
            ).toFixed(3)}{" "}
            pu. Pick the wrong year and your capacity factor — and your whole
            build — is biased.
          </p>
        </>
      )}
    </div>
  );
}
