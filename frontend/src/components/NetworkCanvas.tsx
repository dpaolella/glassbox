import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  GraphData,
  GraphNode,
  GraphResourcePotential,
} from "../api";
import { Selection } from "../App";
import { GLOSSARY } from "../glossary";

// Zone fill/outline colors. The map renders each zone as a filled region
// (polygon) and buses as points inside it — a deliberately *geographic* layout
// rather than a free-floating node graph.
const ZONE_COLORS: Record<string, string> = {
  ZA: "#3b82f6",
  ZB: "#22c55e",
  ZC: "#a855f7",
};
const zoneColor = (zone: string): string => ZONE_COLORS[zone] ?? "#64748b";

interface Props {
  layer: string;
  selection: Selection | null;
  onSelect: (sel: Selection) => void;
}

interface Overlays {
  ac_line: boolean;
  transformer: boolean;
  dc_line: boolean;
  gen: boolean;
  storage: boolean;
  hydro: boolean;
  load: boolean;
  interfaces: boolean;
  candidates: boolean;
  resource_potentials: boolean;
}

const DEFAULT_OVERLAYS: Overlays = {
  ac_line: true,
  transformer: true,
  dc_line: true,
  gen: true,
  storage: true,
  hydro: true,
  load: true,
  interfaces: false,
  candidates: false,
  resource_potentials: false,
};

const CAND_ICON: Record<string, string> = {
  generator: "⚡",
  storage: "🔋",
  line: "〜",
};
const TECH_ICON: Record<string, string> = {
  solar_pv: "☀",
  wind: "🌀",
  battery: "🔋",
};

type Pt = [number, number];

// --- geometry helpers (zone polygons) ---------------------------------------

// Andrew's monotone-chain convex hull.
function convexHull(pts: Pt[]): Pt[] {
  if (pts.length < 3) return pts.slice();
  const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o: Pt, a: Pt, b: Pt) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Pt[] = [];
  for (const q of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0)
      lower.pop();
    lower.push(q);
  }
  const upper: Pt[] = [];
  for (let i = p.length - 1; i >= 0; i--) {
    const q = p[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0)
      upper.pop();
    upper.push(q);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

// Push each hull vertex outward from the centroid so the region wraps its buses
// with a margin; a lone/colinear cluster falls back to a padded box.
function expandedRegion(pts: Pt[], margin: number): Pt[] {
  if (pts.length === 0) return [];
  const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  let hull = convexHull(pts);
  if (hull.length < 3) {
    // degenerate: build a box around the points
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    const y0 = Math.min(...ys);
    const y1 = Math.max(...ys);
    hull = [
      [x0, y0],
      [x1, y0],
      [x1, y1],
      [x0, y1],
    ];
  }
  return hull.map(([x, y]) => {
    const dx = x - cx;
    const dy = y - cy;
    const d = Math.hypot(dx, dy) || 1;
    return [x + (dx / d) * margin, y + (dy / d) * margin] as Pt;
  });
}

// Smooth closed path through vertices (rounded-blob look) via midpoint quadratics.
function smoothClosedPath(pts: Pt[]): string {
  const n = pts.length;
  if (n < 3) return "";
  const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  let d = `M ${mid(pts[n - 1], pts[0]).join(" ")}`;
  for (let i = 0; i < n; i++) {
    const cp = pts[i];
    const next = mid(pts[i], pts[(i + 1) % n]);
    d += ` Q ${cp[0]} ${cp[1]} ${next[0]} ${next[1]}`;
  }
  return d + " Z";
}

function bbox(pts: Pt[]) {
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  return {
    x0: Math.min(...xs),
    x1: Math.max(...xs),
    y0: Math.min(...ys),
    y1: Math.max(...ys),
  };
}

export function NetworkCanvas({ layer, selection, onSelect }: Props) {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [ov, setOv] = useState<Overlays>(DEFAULT_OVERLAYS);
  const [showOverlays, setShowOverlays] = useState(true);
  const [showLegend, setShowLegend] = useState(true);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // pan/zoom transform applied on top of the auto-fitting viewBox
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const pan = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    api.graph().then(setGraph);
  }, []);

  // build options are an `inv`-layer concern: auto-show both the nodal
  // candidates and the zonal resource-potential curves on the capacity layer
  useEffect(() => {
    const on = layer === "inv";
    setOv((o) => ({ ...o, candidates: on, resource_potentials: on }));
  }, [layer]);

  const byId = useMemo(() => {
    const m = new Map<string, GraphNode>();
    graph?.nodes.forEach((n) => m.set(n.id, n));
    return m;
  }, [graph]);

  const interfaceLines = useMemo(() => {
    const s = new Set<string>();
    graph?.interfaces.forEach((i) => i.member_line_ids.forEach((l) => s.add(l)));
    return s;
  }, [graph]);

  // world bounds → viewBox (auto-fit); pan/zoom multiplies on top
  const vb = useMemo(() => {
    if (!graph || graph.nodes.length === 0)
      return { x: 0, y: 0, w: 800, h: 600 };
    const b = bbox(graph.nodes.map((n) => [n.x, n.y] as Pt));
    const pad = 130;
    return {
      x: b.x0 - pad,
      y: b.y0 - pad,
      w: b.x1 - b.x0 + 2 * pad,
      h: b.y1 - b.y0 + 2 * pad,
    };
  }, [graph]);

  // zone regions (smoothed polygons) + label/glyph anchors
  const zoneRegions = useMemo(() => {
    if (!graph) return [];
    return graph.zones.map((z) => {
      const pts = z.member_bus_ids
        .map((id) => byId.get(id))
        .filter((n): n is GraphNode => !!n)
        .map((n) => [n.x, n.y] as Pt);
      const region = expandedRegion(pts, 46);
      const bb = pts.length ? bbox(pts) : { x0: 0, x1: 0, y0: 0, y1: 0 };
      return {
        id: z.id,
        name: z.name,
        path: smoothClosedPath(region),
        cx: (bb.x0 + bb.x1) / 2,
        cy: (bb.y0 + bb.y1) / 2,
        top: bb.y0,
        bottom: bb.y1,
      };
    });
  }, [graph, byId]);

  // resource-potential glyphs placed just *below* each zone (clear of the
  // legend/overlay panels that sit over the top corners), spread horizontally
  const rpGlyphs = useMemo(() => {
    if (!graph) return [] as (GraphResourcePotential & { gx: number; gy: number })[];
    const counts: Record<string, number> = {};
    graph.resource_potentials.forEach((rp) => {
      counts[rp.zone_id] = (counts[rp.zone_id] ?? 0) + 1;
    });
    const seen: Record<string, number> = {};
    return graph.resource_potentials.map((rp) => {
      const zr = zoneRegions.find((z) => z.id === rp.zone_id);
      const i = seen[rp.zone_id] ?? 0;
      seen[rp.zone_id] = i + 1;
      const n = counts[rp.zone_id];
      const gx = (zr?.cx ?? rp.x) + (i - (n - 1) / 2) * 134;
      const gy = (zr?.bottom ?? rp.y) + 42;
      return { ...rp, gx, gy };
    });
  }, [graph, zoneRegions]);

  // screen → viewBox-space coordinate (for cursor-anchored zoom + pan deltas)
  function toLocal(e: { clientX: number; clientY: number }): Pt {
    const svg = svgRef.current!;
    const p = svg.createSVGPoint();
    p.x = e.clientX;
    p.y = e.clientY;
    const m = svg.getScreenCTM();
    if (!m) return [0, 0];
    const lp = p.matrixTransform(m.inverse());
    return [lp.x, lp.y];
  }

  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    const [lx, ly] = toLocal(e);
    const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    setView((v) => {
      const k = Math.min(8, Math.max(0.4, v.k * f));
      const g = k / v.k;
      return { k, x: lx - g * (lx - v.x), y: ly - g * (ly - v.y) };
    });
  }
  function onMouseDown(e: React.MouseEvent) {
    const [lx, ly] = toLocal(e);
    pan.current = { x: lx, y: ly };
  }
  function onMouseMove(e: React.MouseEvent) {
    if (!pan.current) return;
    const [lx, ly] = toLocal(e);
    const px = pan.current.x;
    const py = pan.current.y;
    pan.current = { x: lx, y: ly };
    setView((v) => ({ ...v, x: v.x + (lx - px), y: v.y + (ly - py) }));
  }
  const endPan = () => (pan.current = null);
  const resetView = () => setView({ x: 0, y: 0, k: 1 });

  const transform = `translate(${view.x} ${view.y}) scale(${view.k})`;
  const lw = (w: number) => w / view.k; // keep stroke widths constant on zoom

  return (
    <div className="map-wrap">
      <svg
        ref={svgRef}
        className="net-map"
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        preserveAspectRatio="xMidYMid meet"
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endPan}
        onMouseLeave={endPan}
      >
        <g transform={transform}>
          {/* zone regions */}
          {zoneRegions.map((z) => (
            <g key={z.id}>
              <path
                d={z.path}
                fill={zoneColor(z.id)}
                fillOpacity={0.08}
                stroke={zoneColor(z.id)}
                strokeOpacity={0.5}
                strokeWidth={lw(2)}
              />
              {/* faint region watermark at the centroid (sits behind the buses) */}
              <text
                x={z.cx}
                y={z.cy}
                textAnchor="middle"
                fontSize={lw(20)}
                fontWeight={800}
                fill={zoneColor(z.id)}
                opacity={0.16}
                style={{ textTransform: "uppercase", letterSpacing: lw(2) }}
              >
                {z.name}
              </text>
            </g>
          ))}

          {/* edges */}
          {graph?.edges
            .filter((e) => ov[e.kind as keyof Overlays])
            .map((e) => {
              const a = byId.get(e.from);
              const b = byId.get(e.to);
              if (!a || !b) return null;
              const weak = (e.x ?? 0) >= 0.25;
              const onIface = ov.interfaces && interfaceLines.has(e.id);
              const dc = e.kind === "dc_line";
              let stroke = weak ? "#ef4444" : "#64748b";
              if (onIface) stroke = "#38bdf8";
              return (
                <line
                  key={e.id}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={stroke}
                  strokeWidth={lw(onIface ? 3.5 : 1.8)}
                  strokeDasharray={dc ? `${lw(7)} ${lw(4)}` : undefined}
                  style={{ cursor: "pointer" }}
                  onClick={() =>
                    onSelect({
                      collection: dc
                        ? "dc_lines"
                        : e.kind === "transformer"
                          ? "transformers"
                          : "ac_lines",
                      id: e.id,
                    })
                  }
                >
                  <title>{`${e.id} · ${e.kind} · ${Math.round(e.rating_mva)} MVA`}</title>
                </line>
              );
            })}

          {/* candidate transmission corridors drawn as real (dashed amber) edges */}
          {ov.candidates &&
            graph?.candidates
              .filter((c) => c.kind === "line" && c.from_bus_id && c.to_bus_id)
              .map((c) => {
                const a = byId.get(c.from_bus_id!);
                const b = byId.get(c.to_bus_id!);
                if (!a || !b) return null;
                const sel =
                  selection?.collection === "expansion_candidates" && selection.id === c.id;
                return (
                  <line
                    key={c.id}
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke="#f59e0b"
                    strokeWidth={lw(sel ? 4 : 2.4)}
                    strokeDasharray={`${lw(9)} ${lw(6)}`}
                    style={{ cursor: "pointer" }}
                    onClick={() =>
                      onSelect({ collection: "expansion_candidates", id: c.id })
                    }
                  >
                    <title>{`${c.name} — candidate corridor (≤${Math.round(
                      c.build_max_mw ?? 0,
                    )} MW)`}</title>
                  </line>
                );
              })}

          {/* nodal candidate gens/storage as small amber markers near the bus */}
          {ov.candidates &&
            graph?.candidates
              .filter((c) => c.kind !== "line")
              .map((c) => {
                const sel =
                  selection?.collection === "expansion_candidates" && selection.id === c.id;
                const x = c.x + 20;
                const y = c.y - 20;
                return (
                  <g
                    key={c.id}
                    transform={`translate(${x} ${y})`}
                    style={{ cursor: "pointer" }}
                    onClick={() =>
                      onSelect({ collection: "expansion_candidates", id: c.id })
                    }
                  >
                    <rect
                      x={lw(-9)}
                      y={lw(-9)}
                      width={lw(18)}
                      height={lw(18)}
                      transform={`rotate(45)`}
                      fill="#241a09"
                      stroke="#f59e0b"
                      strokeWidth={lw(sel ? 2.4 : 1.4)}
                    />
                    <text textAnchor="middle" dy={lw(4)} fontSize={lw(11)}>
                      {CAND_ICON[c.kind] ?? "+"}
                    </text>
                    <title>{`${c.name} — candidate ${c.technology} (≤${Math.round(
                      c.build_max_mw ?? 0,
                    )} MW)`}</title>
                  </g>
                );
              })}

          {/* buses as points + label + device badges */}
          {graph?.nodes.map((n) => {
            const sel =
              selection?.collection === "buses" && selection.id === n.id;
            const badges: string[] = [];
            if (ov.gen && n.attached.generators.length)
              badges.push(`⚡${n.attached.generators.length}`);
            if (ov.storage && n.attached.storage.length)
              badges.push(`🔋${n.attached.storage.length}`);
            if (ov.hydro && n.attached.hydro.length)
              badges.push(`💧${n.attached.hydro.length}`);
            if (ov.load && n.attached.loads.length)
              badges.push(`🏠${n.attached.loads.length}`);
            if (n.bus_type === "slack") badges.push("★");
            return (
              <g
                key={n.id}
                style={{ cursor: "pointer" }}
                onClick={() => onSelect({ collection: "buses", id: n.id })}
              >
                {sel && (
                  <circle cx={n.x} cy={n.y} r={lw(11)} fill="none"
                    stroke={zoneColor(n.zone)} strokeWidth={lw(2.5)} />
                )}
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={lw(6)}
                  fill="#0e151f"
                  stroke={zoneColor(n.zone)}
                  strokeWidth={lw(2.2)}
                />
                <text
                  x={n.x}
                  y={n.y - lw(10)}
                  textAnchor="middle"
                  fontSize={lw(11)}
                  fontWeight={600}
                  fill="#e6edf6"
                >
                  {n.id}
                </text>
                {badges.length > 0 && (
                  <text
                    x={n.x}
                    y={n.y + lw(16)}
                    textAnchor="middle"
                    fontSize={lw(10)}
                    fill="#aebcce"
                  >
                    {badges.join(" ")}
                  </text>
                )}
                <title>{`${n.name || n.id} · ${n.zone} · ${Math.round(
                  n.base_kv,
                )} kV`}</title>
              </g>
            );
          })}

          {/* zonal resource-potential glyphs (supply-curve badges above the zone) */}
          {ov.resource_potentials &&
            rpGlyphs.map((rp) => {
              const sel =
                selection?.collection === "resource_potentials" && selection.id === rp.id;
              const maxCx = Math.max(...rp.tranches.map((t) => t.capex_per_mw), 1);
              const bw = 118;
              const bh = 46;
              const hub = byId.get(rp.bus_id ?? "");
              return (
                <g key={rp.id} style={{ cursor: "pointer" }}
                  onClick={() => onSelect({ collection: "resource_potentials", id: rp.id })}>
                  {/* tie line from glyph to its interconnection hub bus */}
                  {hub && (
                    <line x1={rp.gx} y1={rp.gy + bh / 2} x2={hub.x} y2={hub.y}
                      stroke="#f59e0b" strokeOpacity={0.35}
                      strokeWidth={lw(1)} strokeDasharray={`${lw(3)} ${lw(3)}`} />
                  )}
                  <rect
                    x={rp.gx - bw / 2}
                    y={rp.gy}
                    width={bw}
                    height={bh}
                    rx={lw(7)}
                    fill="#211806"
                    stroke="#f59e0b"
                    strokeWidth={lw(sel ? 2.4 : 1.2)}
                    strokeDasharray={`${lw(5)} ${lw(3)}`}
                  />
                  <text x={rp.gx} y={rp.gy + lw(15)} textAnchor="middle"
                    fontSize={lw(11)} fontWeight={700} fill="#fcd9a0">
                    {TECH_ICON[rp.technology] ?? "▤"} {rp.technology}
                  </text>
                  <text x={rp.gx} y={rp.gy + lw(27)} textAnchor="middle"
                    fontSize={lw(9)} fill="#d9b577">
                    {`potential ≤${Math.round(rp.total_build_max_mw)} MW`}
                  </text>
                  {/* mini supply curve: a rising bar per tranche */}
                  {rp.tranches.map((t, i) => {
                    const n = rp.tranches.length;
                    const w = (bw - 24) / n;
                    const h = 6 + 8 * (t.capex_per_mw / maxCx);
                    return (
                      <rect key={i}
                        x={rp.gx - bw / 2 + 12 + i * w}
                        y={rp.gy + bh - 5 - h}
                        width={w - 2}
                        height={h}
                        fill="#f59e0b"
                        fillOpacity={0.45 + 0.18 * i}
                      />
                    );
                  })}
                  <title>{`${rp.name}\nzonal supply curve — best sites first, ≤${Math.round(
                    rp.total_build_max_mw,
                  )} MW total\n${rp.tranches
                    .map(
                      (t, i) =>
                        `  step ${i + 1}: ${Math.round(t.build_max_mw)} MW @ $${Math.round(
                          t.capex_per_mw,
                        ).toLocaleString()}/MW` +
                        (t.expected_capacity_factor != null
                          ? `, CF ${t.expected_capacity_factor}`
                          : "") +
                        (t.lcoe_per_mwh != null ? `, ~$${t.lcoe_per_mwh}/MWh` : ""),
                    )
                    .join("\n")}`}</title>
                </g>
              );
            })}
        </g>
      </svg>

      {/* controls */}
      <div className="map-controls">
        <button onClick={() => setView((v) => ({ ...v, k: Math.min(8, v.k * 1.2) }))} title="Zoom in">＋</button>
        <button onClick={() => setView((v) => ({ ...v, k: Math.max(0.4, v.k / 1.2) }))} title="Zoom out">－</button>
        <button onClick={resetView} title="Reset view">⤾</button>
      </div>

      <div className="canvas-overlays">
        <button className="box-toggle" onClick={() => setShowOverlays((v) => !v)}
          title="Toggle which layers are drawn on the map">
          <span>{showOverlays ? "▾" : "▸"}</span> overlays
        </button>
        {showOverlays &&
          (
            [
              ["ac_line", "AC lines", GLOSSARY.ac_line],
              ["transformer", "transformers", GLOSSARY.transformer],
              ["dc_line", "DC links", GLOSSARY.dc_line],
              ["gen", "generators", GLOSSARY.generators],
              ["storage", "storage", GLOSSARY.storage],
              ["hydro", "hydro", GLOSSARY.hydro],
              ["load", "loads", GLOSSARY.loads],
              ["interfaces", "interfaces (flowgates)", GLOSSARY.interface],
              ["candidates", "build candidates (nodal)", GLOSSARY.expansion_candidates],
              ["resource_potentials", "resource potential (zonal)", GLOSSARY.resource_potential],
            ] as [keyof Overlays, string, string][]
          ).map(([key, label, tip]) => (
            <label key={key} className="overlay-row" title={tip}>
              <input
                type="checkbox"
                checked={ov[key]}
                onChange={(e) => setOv({ ...ov, [key]: e.target.checked })}
              />
              {label}
            </label>
          ))}
      </div>

      <div className="canvas-legend">
        <button className="box-toggle" onClick={() => setShowLegend((v) => !v)} title={GLOSSARY.zone}>
          <span>{showLegend ? "▾" : "▸"}</span> legend
        </button>
        {showLegend && (
          <>
            <div className="legend-title" title={GLOSSARY.zone}>zones (filled regions)</div>
            {graph?.zones.map((z) => (
              <div key={z.id} className="legend-row" title={`${z.name} — ${GLOSSARY.zone}`}>
                <span className="swatch" style={{ background: zoneColor(z.id) }} /> {z.name}
              </div>
            ))}
            <div className="legend-title" style={{ marginTop: 8 }}>on each bus (point)</div>
            <div className="legend-row">
              <span title={GLOSSARY.generators}>⚡ gen</span> &nbsp;
              <span title={GLOSSARY.storage}>🔋 storage</span> &nbsp;
              <span title={GLOSSARY.hydro}>💧 hydro</span>
            </div>
            <div className="legend-row">
              <span title={GLOSSARY.loads}>🏠 load</span> &nbsp;
              <span title={GLOSSARY.slack}>★ slack</span>
            </div>
            <div className="legend-title" style={{ marginTop: 8 }}>build options (inv layer)</div>
            <div className="legend-row" title={GLOSSARY.expansion_candidates}>
              <span className="swatch diamond" style={{ background: "#241a09", borderColor: "#f59e0b" }} />
              candidate (nodal plant/line)
            </div>
            <div className="legend-row" title={GLOSSARY.resource_potential}>
              <span className="swatch" style={{ background: "#211806", border: "1.5px dashed #f59e0b" }} />
              resource potential (zonal)
            </div>
            <div className="legend-row" title={GLOSSARY.weak_feeder}>
              <span className="swatch line" style={{ background: "#ef4444" }} /> weak feeder
            </div>
            <div className="legend-hint">
              scroll to zoom · drag to pan · hover any item for a definition · click
              to inspect ({layer} layer)
            </div>
          </>
        )}
      </div>
    </div>
  );
}
