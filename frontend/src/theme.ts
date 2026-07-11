// Design tokens (issue #36 first pass) — one source of truth for the visual
// language: technology colors used identically on the map, in charts, and in
// legends; scenario A/B colors; result color scales; and the shared Plotly
// theme. Swap values here, everything follows.

// --- technology palette -------------------------------------------------------
// One color per technology, used everywhere a technology is encoded (map icons,
// capacity-mix bars, dispatch stacks, legends). Chosen for dark backgrounds,
// with adjacent-in-merit-order techs kept visually distinct.
export const TECH_COLORS: Record<string, string> = {
  nuclear: "#b07aff", // violet
  coal: "#8d99ae", // slate
  ccgt: "#e8a13c", // amber
  ocgt: "#e8703c", // burnt orange
  gas: "#e8a13c",
  wind: "#4cc9f0", // sky
  solar_pv: "#ffd166", // sun
  solar: "#ffd166",
  hydro: "#3a86ff", // river blue
  battery: "#06d6a0", // mint
  storage: "#06d6a0",
  pumped_hydro: "#4ea8de",
  ldes: "#0ead9a",
  geothermal: "#c1666b",
  biomass: "#7cb518",
  line: "#f59e0b", // buildable-transmission amber (matches candidate styling)
  unserved: "#ef476f", // alarm pink-red
};

export function techColor(tech: string | null | undefined): string {
  if (!tech) return "#64748b";
  return TECH_COLORS[tech] ?? "#64748b";
}

// --- scenario identity ---------------------------------------------------------
// A is always this blue, B always this green — across the diff table, charts,
// and any map rendering of a scenario's results.
export const SCENARIO_A = "#3b82f6";
export const SCENARIO_B = "#22c55e";

// --- status --------------------------------------------------------------------
export const STATUS = {
  good: "#22c55e",
  warn: "#f59e0b",
  bad: "#ef4444",
  neutral: "#64748b",
};

// --- result scales -------------------------------------------------------------

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function rgbCss(rgb: [number, number, number]): string {
  return `rgb(${Math.round(rgb[0])}, ${Math.round(rgb[1])}, ${Math.round(rgb[2])})`;
}

/** Piecewise-linear interpolation through color stops (positions 0..1). */
function ramp(stops: [number, string][]): (t: number) => string {
  const rgb = stops.map(([p, c]) => [p, hexToRgb(c)] as [number, [number, number, number]]);
  return (t: number) => {
    const x = Math.max(0, Math.min(1, t));
    for (let i = 1; i < rgb.length; i++) {
      if (x <= rgb[i][0]) {
        const [p0, c0] = rgb[i - 1];
        const [p1, c1] = rgb[i];
        const u = p1 === p0 ? 0 : (x - p0) / (p1 - p0);
        return rgbCss([lerp(c0[0], c1[0], u), lerp(c0[1], c1[1], u), lerp(c0[2], c1[2], u)]);
      }
    }
    return rgbCss(rgb[rgb.length - 1][1]);
  };
}

// Price scale (LMP): cool blue (cheap) → pale (mid) → hot red (expensive).
// Reads on a dark map and matches the "hot = scarce/expensive" intuition.
export const priceRamp = ramp([
  [0.0, "#2563eb"],
  [0.5, "#94a3b8"],
  [0.75, "#f59e0b"],
  [1.0, "#ef4444"],
]);

/** Normalize a price into the ramp given the run's min/max. */
export function priceColor(value: number, min: number, max: number): string {
  if (!isFinite(value) || max <= min) return priceRamp(0.5);
  return priceRamp((value - min) / (max - min));
}

// Line utilization (|flow| / rating): quiet grey → amber → red as it binds.
export const utilizationRamp = ramp([
  [0.0, "#475569"],
  [0.6, "#64748b"],
  [0.85, "#f59e0b"],
  [1.0, "#ef4444"],
]);

export function utilizationColor(util: number): string {
  return utilizationRamp(util);
}

// --- typography / spacing tokens (mirrored as CSS vars in styles.css) ----------
export const FONT_MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace";

// --- Plotly theme ---------------------------------------------------------------
// One layout template so every chart in the app shares fonts, grid, and legend
// styling (Series, Weather, Scenario Lab, Oracles).
export const PLOTLY_LAYOUT: Record<string, unknown> = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#c9d4e3", size: 11 },
  margin: { l: 48, r: 16, t: 28, b: 40 },
  xaxis: { gridcolor: "#27313f", zerolinecolor: "#3a475a" },
  yaxis: { gridcolor: "#27313f", zerolinecolor: "#3a475a" },
  legend: { orientation: "h", y: -0.2 },
  colorway: [
    SCENARIO_A,
    SCENARIO_B,
    "#f59e0b",
    "#b07aff",
    "#4cc9f0",
    "#ef476f",
    "#06d6a0",
  ],
};
