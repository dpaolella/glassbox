import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  GraphData,
  GraphNode,
  GraphResourcePotential,
  MapResults,
} from "../api";
import { Selection } from "../App";
import { GLOSSARY } from "../glossary";
import { Icon, MapIcon, TECH_ICON_KEY } from "../icons";
import { priceColor, priceRamp, techColor, utilizationColor } from "../theme";

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
  results?: MapResults | null;
  onClearResults?: () => void;
}

interface Overlays {
  results: boolean;
  resource_field: boolean;
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
  results: true,
  resource_field: false,
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

const CAND_ICON_KEY: Record<string, string> = {
  generator: "bolt",
  storage: "battery",
  line: "line",
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

// Smooth open path (Catmull-Rom-ish via midpoint quadratics) for the river.
function smoothOpenPath(pts: Pt[]): string {
  if (pts.length < 2) return "";
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const mx = (pts[i][0] + pts[i + 1][0]) / 2;
    const my = (pts[i][1] + pts[i + 1][1]) / 2;
    d += ` Q ${pts[i][0]} ${pts[i][1]} ${mx} ${my}`;
  }
  const last = pts[pts.length - 1];
  d += ` L ${last[0]} ${last[1]}`;
  return d;
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

// Dispatch-by-technology stacked strip with a playback cursor (issue #27).
function StackStrip({
  stack,
  hour,
  onSeek,
}: {
  stack: { tech: string; series: number[] }[];
  hour: number | null;
  onSeek: (i: number) => void;
}) {
  const W = 400;
  const H = 46;
  const T = stack[0]?.series.length ?? 0;
  if (!T) return null;
  // cumulative layers
  const totals = new Array(T).fill(0);
  stack.forEach(({ series }) => series.forEach((v, i) => (totals[i] += v)));
  const max = Math.max(...totals, 1);
  let base = new Array(T).fill(0);
  const layers = stack.map(({ tech, series }) => {
    const top = base.map((b, i) => b + series[i]);
    const path =
      `M 0 ${H - (base[0] / max) * H} ` +
      top.map((v, i) => `L ${(i / (T - 1)) * W} ${H - (v / max) * H}`).join(" ") +
      ` L ${W} ${H - (base[T - 1] / max) * H} ` +
      base.slice().reverse().map((v, i) => `L ${((T - 1 - i) / (T - 1)) * W} ${H - (v / max) * H}`).join(" ") +
      " Z";
    base = top;
    return { tech, path };
  });
  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      className="stack-strip"
      onPointerDown={(e) => {
        const r = (e.target as SVGElement).closest("svg")!.getBoundingClientRect();
        onSeek(Math.max(0, Math.min(T - 1, Math.round(((e.clientX - r.left) / r.width) * (T - 1)))));
      }}
    >
      <title>dispatch by technology over the window — click to jump</title>
      {layers.map((l) => (
        <path key={l.tech} d={l.path} fill={techColor(l.tech)} fillOpacity={0.85}>
          <title>{l.tech}</title>
        </path>
      ))}
      {hour !== null && (
        <line
          x1={(hour / (T - 1)) * W}
          x2={(hour / (T - 1)) * W}
          y1={0}
          y2={H}
          stroke="var(--text)"
          strokeWidth={1.5}
        />
      )}
    </svg>
  );
}

export function NetworkCanvas({
  layer,
  selection,
  onSelect,
  results: resultsProp,
  onClearResults,
}: Props) {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [ov, setOv] = useState<Overlays>(DEFAULT_OVERLAYS);
  // the overlay toggle hides painted results without discarding them
  const results = ov.results ? resultsProp ?? null : null;
  // start collapsed on short windows so the panels don't bury the map
  const [showOverlays, setShowOverlays] = useState(() => window.innerHeight >= 750);
  const [showLegend, setShowLegend] = useState(() => window.innerHeight >= 750);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // pan/zoom transform applied on top of the auto-fitting viewBox
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const pan = useRef<{ x: number; y: number } | null>(null);

  const [graphErr, setGraphErr] = useState<string | null>(null);
  useEffect(() => {
    api.graph().then(setGraph).catch((e) => setGraphErr(String(e)));
  }, []);

  // build options are an `inv`-layer concern: auto-show both the nodal
  // candidates and the zonal resource-potential curves on the capacity layer
  useEffect(() => {
    const on = layer === "inv";
    setOv((o) => ({ ...o, candidates: on, resource_potentials: on, resource_field: on }));
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

  // when a run's builds arrive, reveal the build-option overlays so the
  // now-solid built candidates are visible whatever layer is active
  useEffect(() => {
    const results = resultsProp;
    if (!results) return;
    const anyBuilds =
      Object.keys(results.builtCapacity).length > 0 ||
      Object.keys(results.builtStoragePower).length > 0 ||
      Object.keys(results.builtTransmission).length > 0 ||
      Object.keys(results.builtResourcePotential).length > 0;
    if (anyBuilds)
      setOv((o) => ({ ...o, candidates: true, resource_potentials: true, results: true }));
  }, [resultsProp]);

  // --- chronological playback (issue #27) -------------------------------
  const T = results?.timesteps?.length ?? 0;
  const [hour, setHour] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(6); // steps per second
  useEffect(() => {
    setHour(null);
    setPlaying(false);
  }, [resultsProp]);
  useEffect(() => {
    if (!playing || T === 0) return;
    const id = window.setInterval(
      () => setHour((h) => ((h ?? -1) + 1) % T),
      1000 / speed,
    );
    return () => window.clearInterval(id);
  }, [playing, speed, T]);

  // weather-in-motion (issue #34): during playback the resource glows pulse
  // with the actual hourly availability of each site's profile
  const [blobSeries, setBlobSeries] = useState<Record<string, number[]> | null>(null);
  useEffect(() => {
    setBlobSeries(null);
    const ts = resultsProp?.timesteps;
    const blobs = graph?.terrain?.resource_blobs ?? [];
    if (!ts || ts.length < 2 || !blobs.length) return;
    // only a contiguous window maps onto an absolute series slice
    for (let i = 1; i < ts.length; i++) if (ts[i] !== ts[i - 1] + 1) return;
    let cancelled = false;
    Promise.all(
      blobs
        .filter((bl) => bl.profile_id)
        .map((bl) =>
          api
            .timeseries(bl.profile_id!, ts[0], ts.length, 1)
            .then((d) => [bl.profile_id!, d.values] as const)
            .catch(() => null),
        ),
    ).then((pairs) => {
      if (cancelled) return;
      const m: Record<string, number[]> = {};
      pairs.forEach((p2) => { if (p2) m[p2[0]] = p2[1]; });
      setBlobSeries(m);
    });
    return () => { cancelled = true; };
  }, [resultsProp, graph]);

  // playback implies weather: reveal the resource field when playing
  useEffect(() => {
    if (hour !== null) setOv((o) => (o.resource_field ? o : { ...o, resource_field: true }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hour !== null]);

  // price per bus: nodal runs key by bus id, zonal runs by zone id (every bus
  // in a zone shows the one flattened price — the aggregation made visible).
  // During playback the per-hour series takes over from the time average.
  const priceOf = useMemo(() => {
    if (!results) return null;
    const p = results.nodalPrice;
    const pt = results.priceT;
    return (n: GraphNode): number | null => {
      if (hour !== null && pt) {
        const series = pt[n.id] ?? pt[n.zone];
        const v = series?.[hour];
        return typeof v === "number" && isFinite(v) ? v : null;
      }
      const v = p[n.id] ?? p[n.zone];
      return typeof v === "number" && isFinite(v) ? v : null;
    };
  }, [results, hour]);

  // robust color range: a few scarcity-priced buses (VOLL duals) would
  // otherwise flatten every other bus to one color — normalize on p10..p90
  const priceRange = useMemo(() => {
    if (!results || !graph || !priceOf) return null;
    // during playback, normalize over ALL hours so colors are comparable
    let vals: number[];
    if (results.priceT && results.timesteps) {
      vals = Object.values(results.priceT)
        .flat()
        .filter((v) => isFinite(v))
        .sort((a, b) => a - b);
    } else {
      vals = graph.nodes
        .map((n) => priceOf(n))
        .filter((v): v is number => v !== null)
        .sort((a, b) => a - b);
    }
    if (!vals.length) return null;
    const q = (p: number) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))];
    const min = q(0.1);
    const max = Math.max(q(0.9), min + 1e-9);
    return { min, max, clipped: min > vals[0] || max < vals[vals.length - 1] };
  }, [results, graph, priceOf]);

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

  // native listener with passive:false — React's synthetic onWheel can be
  // registered passively, making preventDefault a no-op (page scroll leaks)
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const [lx, ly] = toLocal(e);
      const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setView((v) => {
        const k = Math.min(8, Math.max(0.4, v.k * f));
        const g = k / v.k;
        return { k, x: lx - g * (lx - v.x), y: ly - g * (ly - v.y) };
      });
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);
  // pointer events cover mouse AND touch: one pointer pans, two pinch-zoom
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinchDist = useRef<number | null>(null);
  function onPointerDown(e: React.PointerEvent) {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 1) {
      const [lx, ly] = toLocal(e);
      pan.current = { x: lx, y: ly };
    } else {
      pan.current = null;
      const pts = [...pointers.current.values()];
      pinchDist.current = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    }
  }
  function onPointerMove(e: React.PointerEvent) {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2 && pinchDist.current) {
      const pts = [...pointers.current.values()];
      const d = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      const f = d / pinchDist.current;
      pinchDist.current = d;
      const mid = { clientX: (pts[0].x + pts[1].x) / 2, clientY: (pts[0].y + pts[1].y) / 2 };
      const [lx, ly] = toLocal(mid);
      setView((v) => {
        const k = Math.min(8, Math.max(0.4, v.k * f));
        const g = k / v.k;
        return { k, x: lx - g * (lx - v.x), y: ly - g * (ly - v.y) };
      });
      return;
    }
    if (!pan.current) return;
    const [lx, ly] = toLocal(e);
    const px = pan.current.x;
    const py = pan.current.y;
    pan.current = { x: lx, y: ly };
    setView((v) => ({ ...v, x: v.x + (lx - px), y: v.y + (ly - py) }));
  }
  const endPan = (e?: React.PointerEvent) => {
    if (e) pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinchDist.current = null;
    if (pointers.current.size === 0) pan.current = null;
  };
  const resetView = () => setView({ x: 0, y: 0, k: 1 });

  const transform = `translate(${view.x} ${view.y}) scale(${view.k})`;
  const lw = (w: number) => w / view.k; // keep stroke widths constant on zoom

  return (
    <div className="map-wrap">
      {!graph && !graphErr && <div className="map-status">loading map…</div>}
      {graphErr && (
        <div className="map-status error">
          couldn't load the network: {graphErr} — is the backend running?
        </div>
      )}
      <svg
        ref={svgRef}
        className="net-map"
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        preserveAspectRatio="xMidYMid meet"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        onPointerLeave={() => { pointers.current.clear(); pan.current = null; pinchDist.current = null; }}
        style={{ touchAction: "none" }}
      >
        <g transform={transform}>
          {/* procedural terrain (issue #26): landmass, river, resource field */}
          {graph?.terrain && (
            <g>
              <defs>
                <radialGradient id="rg-wind">
                  <stop offset="0%" stopColor="#4cc9f0" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#4cc9f0" stopOpacity="0" />
                </radialGradient>
                <radialGradient id="rg-solar">
                  <stop offset="0%" stopColor="#ffd166" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#ffd166" stopOpacity="0" />
                </radialGradient>
              </defs>
              <path
                d={smoothClosedPath(graph.terrain.land as Pt[])}
                fill="var(--map-land)"
                stroke="var(--map-coast)"
                strokeWidth={lw(2)}
              />
              {graph.terrain.river.length > 1 && (
                <path
                  d={smoothOpenPath(graph.terrain.river as Pt[])}
                  fill="none"
                  stroke="var(--map-river)"
                  strokeWidth={lw(4)}
                  strokeLinecap="round"
                  opacity={0.75}
                >
                  <title>river — feeds the hydro reservoir</title>
                </path>
              )}
              {ov.resource_field &&
                graph.terrain.resource_blobs.map((bl, i) => {
                  // live weather: pulse with this hour's availability
                  const series = bl.profile_id ? blobSeries?.[bl.profile_id] : undefined;
                  const avail = hour !== null && series ? series[hour] : null;
                  const o = avail !== null ? 0.12 + 0.9 * avail : bl.intensity;
                  const r = bl.r * (0.7 + 0.5 * (avail !== null ? avail : bl.intensity));
                  return (
                    <circle key={i} cx={bl.x} cy={bl.y} r={r}
                      fill={`url(#rg-${bl.kind})`} opacity={Math.min(1, o)}>
                      {avail !== null && (
                        <title>{`${bl.kind} availability now: ${(avail * 100).toFixed(0)}%`}</title>
                      )}
                    </circle>
                  );
                })}
            </g>
          )}
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
              let width = onIface ? 3.5 : 1.8;
              let flowTip = "";
              // results: color+weight lines by average loading (nodal runs
              // report per-line flows; zonal transport corridors don't map)
              let flow = results?.flows?.[e.id];
              if (hour !== null && results?.flowT?.[e.id])
                flow = Math.abs(results.flowT[e.id][hour]);
              if (flow !== undefined && e.rating_mva > 0) {
                const util = Math.min(1, flow / e.rating_mva);
                stroke = utilizationColor(util);
                width = 1.5 + 4 * util;
                flowTip = ` · avg |flow| ${Math.round(flow)} MW (${Math.round(util * 100)}% of rating)`;
              }
              return (
                <line
                  key={e.id}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={stroke}
                  strokeWidth={lw(width)}
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
                  <title>{`${e.id} · ${e.kind} · ${Math.round(e.rating_mva)} MVA${flowTip}`}</title>
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
                const built = results?.builtTransmission?.[c.id];
                const mx = (a.x + b.x) / 2;
                const my = (a.y + b.y) / 2;
                return (
                  <g key={c.id} style={{ cursor: "pointer" }}
                    onClick={() =>
                      onSelect({ collection: "expansion_candidates", id: c.id })
                    }>
                    <line
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke="#f59e0b"
                      strokeWidth={lw(built ? 4.5 : sel ? 4 : 2.4)}
                      strokeDasharray={built ? undefined : `${lw(9)} ${lw(6)}`}
                    />
                    {built && (
                      <g>
                        <rect x={mx - lw(34)} y={my - lw(20)} width={lw(68)} height={lw(15)}
                          rx={lw(4)} fill="#f59e0b" />
                        <text x={mx} y={my - lw(9)} textAnchor="middle"
                          fontSize={lw(10)} fontWeight={700} fill="#221503">
                          +{Math.round(built)} MW
                        </text>
                      </g>
                    )}
                    <title>{`${c.name} — ${
                      built
                        ? `BUILT ${Math.round(built)} MW of ≤${Math.round(c.build_max_mw ?? 0)} MW`
                        : `candidate corridor (≤${Math.round(c.build_max_mw ?? 0)} MW)`
                    }`}</title>
                  </g>
                );
              })}

          {/* nodal candidate gens/storage as small amber markers near the bus */}
          {ov.candidates &&
            graph?.candidates
              .filter((c) => c.kind !== "line")
              .map((c) => {
                const sel =
                  selection?.collection === "expansion_candidates" && selection.id === c.id;
                const built =
                  results?.builtCapacity?.[c.id] ?? results?.builtStoragePower?.[c.id];
                const x = c.x + lw(20);
                const y = c.y - lw(20);
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
                      fill={built ? "#f59e0b" : "#241a09"}
                      stroke="#f59e0b"
                      strokeWidth={lw(sel ? 2.4 : 1.4)}
                    />
                    <MapIcon icon={CAND_ICON_KEY[c.kind] ?? "bolt"} x={0} y={0}
                      size={lw(11)} color={built ? "#221503" : "#f59e0b"} />
                    {built && (
                      <text textAnchor="middle" y={lw(24)} fontSize={lw(10)}
                        fontWeight={700} fill="#fcd9a0">
                        +{Math.round(built)} MW
                      </text>
                    )}
                    <title>{`${c.name} — ${
                      built
                        ? `BUILT ${Math.round(built)} MW of ≤${Math.round(c.build_max_mw ?? 0)} MW`
                        : `candidate ${c.technology} (≤${Math.round(c.build_max_mw ?? 0)} MW)`
                    }`}</title>
                  </g>
                );
              })}

          {/* buses as points + label + device badges */}
          {graph?.nodes.map((n) => {
            const sel =
              selection?.collection === "buses" && selection.id === n.id;
            const badges: { icon: string; count: number | null; color: string }[] = [];
            if (ov.gen && n.attached.generators.length)
              badges.push({ icon: "bolt", count: n.attached.generators.length, color: "#e8a13c" });
            if (ov.storage && n.attached.storage.length)
              badges.push({ icon: "battery", count: n.attached.storage.length, color: "#06d6a0" });
            if (ov.hydro && n.attached.hydro.length)
              badges.push({ icon: "drop", count: n.attached.hydro.length, color: "#3a86ff" });
            if (ov.load && n.attached.loads.length)
              badges.push({ icon: "house", count: n.attached.loads.length, color: "var(--muted)" });
            if (n.bus_type === "slack")
              badges.push({ icon: "star", count: null, color: "#ffd166" });
            const unserved =
              hour !== null && results?.unservedT
                ? (results.unservedT[n.id]?.[hour] ?? 0) * 10 // MW now (scaled to trip the >1 gate)
                : results?.unservedMwh?.[n.id] ?? 0;
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
                {unserved > 1 && (
                  <circle cx={n.x} cy={n.y} r={lw(12.5)} fill="none"
                    stroke="#ef4444" strokeWidth={lw(1.8)}
                    strokeDasharray={`${lw(3)} ${lw(3)}`} />
                )}
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={lw(priceOf && priceRange && priceOf(n) !== null ? 7.5 : 6)}
                  fill={
                    priceOf && priceRange && priceOf(n) !== null
                      ? priceColor(priceOf(n)!, priceRange.min, priceRange.max)
                      : "var(--map-node-fill)"
                  }
                  stroke={zoneColor(n.zone)}
                  strokeWidth={lw(2.2)}
                />
                <text
                  x={n.x}
                  y={n.y - lw(10)}
                  textAnchor="middle"
                  fontSize={lw(11)}
                  fontWeight={600}
                  fill="var(--text)"
                >
                  {n.id}
                </text>
                {n.attached.loads.length > 0 && n.name && (
                  <text
                    x={n.x}
                    y={n.y - lw(21)}
                    textAnchor="middle"
                    fontSize={lw(9.5)}
                    fontStyle="italic"
                    fill="var(--muted)"
                  >
                    {n.name}
                  </text>
                )}
                {badges.length > 0 &&
                  badges.map((b, i) => {
                    const widths = badges.map((bb) => lw(bb.count !== null ? 17 : 11));
                    const total = widths.reduce((a, x) => a + x, 0);
                    const x0 = n.x - total / 2 + widths.slice(0, i).reduce((a, x) => a + x, 0);
                    return (
                      <g key={b.icon}>
                        <MapIcon icon={b.icon} x={x0 + lw(5)} y={n.y + lw(15)}
                          size={lw(9)} color={b.color} />
                        {b.count !== null && (
                          <text x={x0 + lw(11)} y={n.y + lw(18.5)} fontSize={lw(9.5)}
                            fill="var(--muted)">
                            {b.count}
                          </text>
                        )}
                      </g>
                    );
                  })}
                <title>{`${n.name || n.id} · ${n.zone} · ${Math.round(n.base_kv)} kV${
                  priceOf && priceOf(n) !== null
                    ? ` · price $${priceOf(n)!.toFixed(1)}/MWh${results?.spatial === "aggregate" ? " (zonal — one flat price per zone)" : ""}`
                    : ""
                }${unserved > 1 ? ` · ⚠ ~${Math.round(unserved).toLocaleString()} MWh/yr unserved` : ""}`}</title>
              </g>
            );
          })}

          {/* zonal resource-potential glyphs (supply-curve badges above the zone) */}
          {ov.resource_potentials &&
            rpGlyphs.map((rp) => {
              const sel =
                selection?.collection === "resource_potentials" && selection.id === rp.id;
              const maxCx = Math.max(...rp.tranches.map((t) => t.capex_per_mw), 1);
              const bw = lw(118);
              const bh = lw(46);
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
                    strokeWidth={lw(sel ? 2.4 : results?.builtResourcePotential?.[rp.id] ? 2 : 1.2)}
                    strokeDasharray={
                      results?.builtResourcePotential?.[rp.id] ? undefined : `${lw(5)} ${lw(3)}`
                    }
                  />
                  <MapIcon icon={TECH_ICON_KEY[rp.technology] ?? "bolt"}
                    x={rp.gx - lw(6) - (rp.technology.length * lw(6.6)) / 2}
                    y={rp.gy + lw(11)} size={lw(10)} color="#fcd9a0" />
                  <text x={rp.gx + lw(6)} y={rp.gy + lw(15)} textAnchor="middle"
                    fontSize={lw(11)} fontWeight={700} fill="#fcd9a0">
                    {rp.technology}
                  </text>
                  <text x={rp.gx} y={rp.gy + lw(27)} textAnchor="middle"
                    fontSize={lw(9)} fontWeight={results?.builtResourcePotential?.[rp.id] ? 700 : 400}
                    fill={results?.builtResourcePotential?.[rp.id] ? "#fcd9a0" : "#d9b577"}>
                    {results?.builtResourcePotential?.[rp.id]
                      ? `built ${Math.round(results.builtResourcePotential[rp.id])} of ${Math.round(rp.total_build_max_mw)} MW`
                      : `potential ≤${Math.round(rp.total_build_max_mw)} MW`}
                  </text>
                  {/* mini supply curve: a rising bar per tranche */}
                  {rp.tranches.map((t, i) => {
                    const n = rp.tranches.length;
                    const w = (bw - lw(24)) / n;
                    const h = lw(6 + 8 * (t.capex_per_mw / maxCx));
                    return (
                      <rect key={i}
                        x={rp.gx - bw / 2 + lw(12) + i * w}
                        y={rp.gy + bh - lw(5) - h}
                        width={w - lw(2)}
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

      {/* day/night tint during playback */}
      {hour !== null && results?.timesteps && (() => {
        const hod = results.timesteps[hour] % 24;
        const night = hod < 6 || hod >= 20 ? 0.28 : hod < 8 || hod >= 18 ? 0.12 : 0;
        return night > 0 ? (
          <div className="day-tint" style={{ opacity: night }} />
        ) : null;
      })()}

      {/* chronological playback (issue #27) */}
      {results?.timesteps && results.timesteps.length > 1 && (
        <div className="playback-panel">
          <div className="playback-controls">
            <button
              className="pb-btn"
              title={playing ? "pause" : "play the solved window hour by hour"}
              onClick={() => {
                if (hour === null) setHour(0);
                setPlaying((p) => !p);
              }}
            >
              {playing ? "❚❚" : "▶"}
            </button>
            <button
              className="pb-btn"
              title="playback speed (steps per second)"
              onClick={() => setSpeed((sp) => (sp === 2 ? 6 : sp === 6 ? 14 : 2))}
            >
              {speed}×
            </button>
            <input
              type="range"
              min={0}
              max={T - 1}
              value={hour ?? 0}
              onChange={(e) => {
                setHour(Number(e.target.value));
              }}
              className="pb-slider"
            />
            <span className="pb-label" title="hour of the solved window (day of year · time of day)">
              {hour !== null
                ? `d${Math.floor((results.timesteps[hour] % 8760) / 24) + 1} ${String(results.timesteps[hour] % 24).padStart(2, "0")}:00`
                : `${T} h window`}
            </span>
            {hour !== null && (
              <button className="pb-btn" title="exit playback (back to time-averaged view)"
                onClick={() => { setPlaying(false); setHour(null); }}>
                ✕
              </button>
            )}
          </div>
          {results.stack && (
            <StackStrip stack={results.stack} hour={hour}
              onSeek={(i) => { setHour(i); }} />
          )}
        </div>
      )}

      {/* active results banner */}
      {results && (
        <div className="map-results-banner">
          <span className="results-dot" />
          <span className="results-label" title="Solved-run results are painted on the map: bus color = average price, line width/color = average loading, solid amber = built.">
            {results.label}
          </span>
          {priceRange && (
            <span
              className="results-scale"
              title={
                priceRange.clipped
                  ? "Color scale spans the 10th–90th percentile of bus prices; extreme scarcity-priced buses clamp to the ends."
                  : "Color scale spans the range of average bus prices."
              }
            >
              <span>{priceRange.clipped ? "≤" : ""}${priceRange.min.toFixed(0)}</span>
              <span
                className="scale-bar"
                style={{
                  background: `linear-gradient(90deg, ${priceRamp(0)}, ${priceRamp(0.5)}, ${priceRamp(0.75)}, ${priceRamp(1)})`,
                }}
              />
              <span>{priceRange.clipped ? "≥" : ""}${priceRange.max.toFixed(0)}/MWh</span>
            </span>
          )}
          {onClearResults && (
            <button className="results-clear" onClick={onClearResults}
              title="Remove results from the map">
              ✕
            </button>
          )}
        </div>
      )}

      <div className="canvas-overlays">
        <button className="box-toggle" onClick={() => setShowOverlays((v) => !v)}
          title="Toggle which layers are drawn on the map">
          <span>{showOverlays ? "▾" : "▸"}</span> overlays
        </button>
        {showOverlays && resultsProp && (
          <label className="overlay-row" title="Show or hide the painted solved-run results (prices, flows, builds) without discarding them.">
            <input
              type="checkbox"
              checked={ov.results}
              onChange={(e) => setOv({ ...ov, results: e.target.checked })}
            />
            results (prices/flows/builds)
          </label>
        )}
        {showOverlays && graph?.terrain && (
          <label className="overlay-row" title="Soft shading where the wind/solar resource is strongest — derived from each weather site's resource quality (the same physics the CEM sees).">
            <input
              type="checkbox"
              checked={ov.resource_field}
              onChange={(e) => setOv({ ...ov, resource_field: e.target.checked })}
            />
            resource field (wind/solar)
          </label>
        )}
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
              <span title={GLOSSARY.generators}><Icon icon="bolt" color="#e8a13c" /> gen</span> &nbsp;
              <span title={GLOSSARY.storage}><Icon icon="battery" color="#06d6a0" /> storage</span> &nbsp;
              <span title={GLOSSARY.hydro}><Icon icon="drop" color="#3a86ff" /> hydro</span>
            </div>
            <div className="legend-row">
              <span title={GLOSSARY.loads}><Icon icon="house" /> load</span> &nbsp;
              <span title={GLOSSARY.slack}><Icon icon="star" color="#ffd166" /> slack</span>
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
            <div className="legend-title" style={{ marginTop: 8 }}>lines</div>
            <div className="legend-row" title={GLOSSARY.interface}>
              <span className="swatch line" style={{ background: "#38bdf8" }} />
              interface member (overlay on)
            </div>
            <div className="legend-row" title={GLOSSARY.dc_line}>
              <span className="swatch line" style={{ background: "#64748b", borderTop: "2px dashed #64748b", height: 0 }} />
              dashed grey = DC link
            </div>
            <div className="legend-row" title={GLOSSARY.candidate}>
              <span className="swatch line" style={{ background: "transparent", borderTop: "2px dashed #f59e0b", height: 0 }} />
              dashed amber = candidate corridor
            </div>
            <div className="legend-row" title={`${GLOSSARY.weak_feeder} Drawn red when series reactance ≥ 0.25 pu.`}>
              <span className="swatch line" style={{ background: "#ef4444" }} /> weak feeder (x ≥ 0.25 pu)
            </div>
            {results && (
              <>
                <div className="legend-title" style={{ marginTop: 8 }}>
                  results on map
                </div>
                <div className="legend-row" title="Bus fill = time-averaged price at that bus (zonal runs: one flat price per zone).">
                  <span className="swatch" style={{ background: priceRamp(0.9) }} />
                  bus color = avg price
                </div>
                <div className="legend-row" title="Line width and color = time-averaged |flow| as a share of the line's rating; red = binding.">
                  <span className="swatch line" style={{ background: utilizationColor(0.95) }} />
                  line width = loading
                </div>
                <div className="legend-row" title="A solid amber corridor/diamond is a build the capacity-expansion run chose; dashed = unbuilt option.">
                  <span className="swatch line" style={{ background: "#f59e0b" }} />
                  solid amber = built
                </div>
                <div className="legend-row" title="A dashed red ring marks a bus with unserved energy in this run — load the solve chose (or was forced) to shed.">
                  <span className="swatch" style={{ background: "transparent", border: "1.5px dashed #ef4444", borderRadius: "50%" }} />
                  red ring = unserved energy
                </div>
              </>
            )}
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
