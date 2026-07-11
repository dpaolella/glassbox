import { useEffect, useMemo, useState } from "react";
import { api, ScenarioDiffResult } from "../api";
import { SCENARIO_A, SCENARIO_B, techColor } from "../theme";
import { Plot } from "./Plot";

// IRP-style dashboard (issue #31): the canonical chart vocabulary of real
// planning studies — duration curves, screening curves, cost decomposition,
// and the supply curves with what the CEM actually chose to build.

function crf(rate: number, life: number): number {
  const f = Math.pow(1 + rate, life);
  return (rate * f) / (f - 1);
}

// duration curve helper: sorted descending
const durationSort = (xs: number[]) => [...xs].sort((a, b) => b - a);

export function Dashboard({ result }: { result: ScenarioDiffResult }) {
  const [cands, setCands] = useState<Record<string, unknown>[]>([]);
  const [rps, setRps] = useState<Record<string, unknown>[]>([]);
  const [fuels, setFuels] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    api.listEntities("expansion_candidates").then(setCands).catch(() => {});
    api.listEntities("resource_potentials").then(setRps).catch(() => {});
    api.listEntities("fuels").then(setFuels).catch(() => {});
  }, []);

  // --- duration curves (from run B's chronology, when available) ----------
  const duration = useMemo(() => {
    const r = result.b.result as Record<string, any>;
    const disp = r.operational ?? r.dispatch;
    const net = r.network;
    if (!disp?.timesteps?.length) return null;
    const T = disp.timesteps.length;
    const total = new Array(T).fill(0);
    const vre = new Array(T).fill(0);
    for (const [gid, series] of Object.entries(
      (disp.generation_mw ?? {}) as Record<string, number[]>,
    )) {
      series.forEach((v, i) => (total[i] += v));
      if (/wind|solar/.test(gid)) series.forEach((v, i) => (vre[i] += v));
    }
    // price duration: bus-average per hour
    let price: number[] | null = null;
    if (net?.nodal_price_t) {
      const seriesList = Object.values(net.nodal_price_t as Record<string, number[]>);
      if (seriesList.length) {
        price = new Array(T).fill(0);
        seriesList.forEach((s) => s.forEach((v, i) => (price![i] += v / seriesList.length)));
      }
    }
    return {
      served: durationSort(total),
      netLoad: durationSort(total.map((v, i) => v - vre[i])),
      price: price ? durationSort(price) : null,
      T,
    };
  }, [result]);

  // --- screening curves (annualized $/kW-yr vs capacity factor) -----------
  const screening = useMemo(() => {
    const fuelPrice: Record<string, number> = {};
    fuels.forEach((f) => {
      fuelPrice[String(f.id)] = Number(f.price_per_mmbtu ?? 0);
    });
    const seen = new Map<string, { capex: number; fom: number; life: number; mc: number }>();
    const add = (tech: string, capex: number, fom: number, life: number, mc: number) => {
      if (!capex || seen.has(tech)) return;
      seen.set(tech, { capex, fom, life, mc });
    };
    cands.forEach((c) => {
      if (c.kind !== "generator") return;
      const hr = Number(c.heat_rate_mmbtu_per_mwh ?? 0);
      const mc = hr * (fuelPrice[String(c.fuel_id)] ?? 0) + Number(c.vom_per_mwh ?? 0);
      add(String(c.technology), Number(c.capex_per_mw), Number(c.fom_per_mw_yr ?? 0),
          Number(c.lifetime_yr ?? 30), mc);
    });
    rps.forEach((rp) => {
      if (rp.kind !== "generator") return;
      const tr = (rp.tranches as Record<string, unknown>[] | undefined)?.[0];
      if (!tr) return;
      add(String(rp.technology), Number(tr.capex_per_mw), Number(rp.fom_per_mw_yr ?? 0),
          Number(rp.lifetime_yr ?? 25), Number(rp.vom_per_mwh ?? 0));
    });
    const cf = Array.from({ length: 20 }, (_, i) => (i + 1) * 0.05);
    return Array.from(seen.entries()).map(([tech, p]) => ({
      tech,
      x: cf,
      // $/kW-yr: annualized fixed + variable energy cost at that CF
      y: cf.map((c) => ((p.capex * crf(0.07, p.life) + p.fom) / 1000) + (c * 8760 * p.mc) / 1000),
    }));
  }, [cands, rps, fuels]);

  // --- cost decomposition (CEM runs) ---------------------------------------
  const costs = useMemo(() => {
    const a = (result.a.result as Record<string, any>).cost_breakdown;
    const b = (result.b.result as Record<string, any>).cost_breakdown;
    if (!a && !b) return null;
    const keys = Array.from(new Set([...Object.keys(a ?? {}), ...Object.keys(b ?? {})]));
    return { keys, a: keys.map((k) => a?.[k] ?? 0), b: keys.map((k) => b?.[k] ?? 0) };
  }, [result]);

  // --- supply curves with the built quantity shaded -------------------------
  const built = (result.b.result as Record<string, any>).built_resource_potential_mw ?? {};
  const supply = useMemo(() => {
    return rps
      .filter((rp) => rp.kind === "generator" || rp.kind === "storage")
      .map((rp) => {
        const tranches = (rp.tranches ?? []) as Record<string, unknown>[];
        let cum = 0;
        const xs: number[] = [0];
        const ys: number[] = [Number(tranches[0]?.capex_per_mw ?? 0) / 1e6];
        tranches.forEach((t) => {
          const c = Number(t.capex_per_mw) / 1e6;
          ys[ys.length - 1] = ys[ys.length - 1] || c;
          xs.push((cum += Number(t.build_max_mw)));
          ys.push(c);
        });
        return {
          id: String(rp.id), tech: String(rp.technology),
          xs, ys, builtMw: Number(built[String(rp.id)] ?? 0),
        };
      });
  }, [rps, built]);

  return (
    <div className="dashboard">
      {duration && (
        <>
          <h4 title="Every hour of the solved window sorted from highest to lowest — the classic view of peakiness. The gap between the two curves is what wind+solar carried; the steep 'net load' left edge is what firm resources must still cover.">
            Load duration curves (MW)
          </h4>
          <Plot
            height={220}
            data={[
              { x: duration.served.map((_, i) => (100 * i) / duration.T),
                y: duration.served, name: "served load",
                line: { color: "#8a99ad", width: 2 } },
              { x: duration.netLoad.map((_, i) => (100 * i) / duration.T),
                y: duration.netLoad, name: "net of wind+solar",
                line: { color: techColor("wind"), width: 2 } },
            ]}
            layout={{ xaxis: { title: "% of hours" }, yaxis: { title: "MW" } }}
          />
          {duration.price && (
            <>
              <h4 title="Bus-average price for every hour, sorted. A long flat body priced at fuel cost with a thin scarcity spike on the left is the signature of an energy-only market.">
                Price duration curve ($/MWh)
              </h4>
              <Plot
                height={200}
                data={[{
                  x: duration.price.map((_, i) => (100 * i) / duration.T),
                  y: duration.price, name: "avg price",
                  line: { color: "#f59e0b", width: 2 },
                }]}
                layout={{ xaxis: { title: "% of hours" },
                          yaxis: { title: "$/MWh", type: "log" } }}
              />
            </>
          )}
        </>
      )}

      {screening.length > 0 && (
        <>
          <h4 title="THE classic capacity-planning chart: total annual cost per kW of each technology as a function of how many hours it runs. Crossover points say which technology is cheapest at each capacity factor — peakers win at low CF, baseload at high CF.">
            Screening curves ($/kW-yr vs capacity factor)
          </h4>
          <Plot
            height={230}
            data={screening.map((sc) => ({
              x: sc.x, y: sc.y, name: sc.tech,
              line: { color: techColor(sc.tech), width: 2 },
            }))}
            layout={{ xaxis: { title: "capacity factor" },
                      yaxis: { title: "$/kW-yr" } }}
          />
        </>
      )}

      {costs && (costs.a.some((v) => v > 0) || costs.b.some((v) => v > 0)) && (
        <>
          <h4 title="Total annual system cost decomposed — where each scenario's money actually goes.">
            Cost decomposition ($/yr)
          </h4>
          <Plot
            height={200}
            data={[
              { x: costs.keys.map((k) => k.replace(/_/g, " ")), y: costs.a,
                name: "A", type: "bar", marker: { color: SCENARIO_A } },
              { x: costs.keys.map((k) => k.replace(/_/g, " ")), y: costs.b,
                name: "B", type: "bar", marker: { color: SCENARIO_B } },
            ]}
            layout={{ barmode: "group", yaxis: { title: "$/yr" } }}
          />
        </>
      )}

      {supply.length > 0 && (
        <>
          <h4 title="Each zonal resource as a stepped supply curve (capacity at rising capex — best sites first). The dashed vertical line marks how far up the curve scenario B's capacity expansion actually built.">
            Resource supply curves (built vs potential)
          </h4>
          <Plot
            height={230}
            data={[
              ...supply.map((sc) => ({
                x: sc.xs, y: sc.ys, name: sc.id.replace("rp_", ""),
                line: { color: techColor(sc.tech), width: 2, shape: "hv" as const },
              })),
              ...supply
                .filter((sc) => sc.builtMw > 0)
                .map((sc) => ({
                  x: [sc.builtMw, sc.builtMw],
                  y: [0, Math.max(...sc.ys) * 1.05],
                  name: `${sc.id.replace("rp_", "")} built`,
                  mode: "lines" as const, showlegend: false,
                  line: { color: techColor(sc.tech), width: 1.5, dash: "dot" as const },
                })),
            ]}
            layout={{ xaxis: { title: "cumulative MW" },
                      yaxis: { title: "capex $M/MW" } }}
          />
        </>
      )}
    </div>
  );
}
