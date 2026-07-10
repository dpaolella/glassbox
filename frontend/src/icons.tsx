// Small single-path icon set (issue #36) — replaces emoji so glyphs scale
// crisply, print well, and can carry the technology color. Paths are drawn in
// a 0..12 box; render at any size via `size`.

export const ICON_PATHS: Record<string, string> = {
  // lightning bolt (generator)
  bolt: "M7 0 L2 7 H5 L4 12 L10 4.5 H6.5 Z",
  // battery (storage)
  battery: "M3 2 H9 V3.2 H10 V10 H2 V3.2 H3 Z M4.5 4.5 H7.5 V8.5 H4.5 Z",
  // water drop (hydro)
  drop: "M6 0 C6 0 1.8 5 1.8 8 A4.2 4.2 0 0 0 10.2 8 C10.2 5 6 0 6 0 Z",
  // house (load)
  house: "M6 1 L11 5.5 H9.5 V11 H7 V8 H5 V11 H2.5 V5.5 H1 Z",
  // star (slack bus)
  star: "M6 0.5 L7.4 4.2 L11.4 4.4 L8.3 6.9 L9.3 10.8 L6 8.6 L2.7 10.8 L3.7 6.9 L0.6 4.4 L4.6 4.2 Z",
  // sun (solar)
  sun: "M6 3.4 A2.6 2.6 0 1 0 6 8.6 A2.6 2.6 0 1 0 6 3.4 Z M5.6 0 H6.4 V2 H5.6 Z M5.6 10 H6.4 V12 H5.6 Z M0 5.6 H2 V6.4 H0 Z M10 5.6 H12 V6.4 H10 Z M1.6 2.2 L2.2 1.6 L3.6 3 L3 3.6 Z M8.4 9 L9 8.4 L10.4 9.8 L9.8 10.4 Z M9.8 1.6 L10.4 2.2 L9 3.6 L8.4 3 Z M2.2 10.4 L1.6 9.8 L3 8.4 L3.6 9 Z",
  // turbine (wind)
  wind: "M6 5.1 A1 1 0 0 1 6.9 6.4 L6.4 12 H5.6 L5.1 6.4 A1 1 0 0 1 6 5.1 Z M6.6 5 L10.8 3.4 A0.9 0.9 0 0 0 9.6 2 L6.2 4.6 A1.4 1.4 0 0 1 6.6 5 Z M5.4 5 L1.2 3.4 A0.9 0.9 0 0 1 2.4 2 L5.8 4.6 A1.4 1.4 0 0 0 5.4 5 Z M6 3.9 L5.5 0.4 A0.9 0.9 0 0 1 6.5 0.4 L6 3.9 Z",
  // flame (thermal / gas)
  flame: "M6 0 C7 2.5 9.5 4 9.5 7.4 A3.5 3.5 0 0 1 2.5 7.4 C2.5 5.8 3.3 4.7 4.2 3.6 C4.4 4.6 4.9 5.2 5.6 5.6 C5.2 3.6 5.6 1.6 6 0 Z",
  // corridor / line (transmission candidate)
  line: "M0 9 L4 5 H2.8 L6.2 1.6 L7 2.4 L4.8 4.6 H6 L2 8.6 H3.4 L0.8 11.2 Z M6 11 L11 6 H12 V7 L7 12 H6 Z",
};

// technology -> icon key
export const TECH_ICON_KEY: Record<string, string> = {
  wind: "wind",
  solar_pv: "sun",
  solar: "sun",
  battery: "battery",
  storage: "battery",
  hydro: "drop",
  ccgt: "flame",
  ocgt: "flame",
  gas: "flame",
  coal: "flame",
  nuclear: "bolt",
  line: "line",
};

/** SVG-context icon (for the map): a positioned, scaled <path>. */
export function MapIcon({
  icon,
  x,
  y,
  size,
  color,
}: {
  icon: string;
  x: number; // center x
  y: number; // center y
  size: number;
  color: string;
}) {
  const d = ICON_PATHS[icon];
  if (!d) return null;
  const s = size / 12;
  return (
    <path
      d={d}
      transform={`translate(${x - size / 2} ${y - size / 2}) scale(${s})`}
      fill={color}
    />
  );
}

/** HTML-context icon (legend rows, panels). */
export function Icon({
  icon,
  size = 12,
  color = "currentColor",
  title,
}: {
  icon: string;
  size?: number;
  color?: string;
  title?: string;
}) {
  const d = ICON_PATHS[icon];
  if (!d) return null;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 12"
      style={{ verticalAlign: "-1px", flex: "0 0 auto" }}
    >
      {title && <title>{title}</title>}
      <path d={d} fill={color} />
    </svg>
  );
}
