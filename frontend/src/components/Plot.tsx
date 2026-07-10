import { useEffect, useRef, useState } from "react";
import Plotly from "plotly.js-dist-min";
import { PLOTLY_LAYOUT } from "../theme";

interface PlotProps {
  data: Record<string, unknown>[];
  layout?: Record<string, unknown>;
  height?: number;
}

// merge axis objects so per-chart overrides keep the theme's grid styling;
// font/grid colors are read from the CSS tokens so charts follow the active
// theme (dark / light projector mode)
function mergeLayout(overrides?: Record<string, unknown>) {
  const css = getComputedStyle(document.documentElement);
  const text = css.getPropertyValue("--text").trim() || "#c9d4e3";
  const grid = css.getPropertyValue("--border").trim() || "#27313f";
  const themed: Record<string, unknown> = {
    ...PLOTLY_LAYOUT,
    font: { color: text, size: 11 },
    xaxis: { gridcolor: grid, zerolinecolor: grid },
    yaxis: { gridcolor: grid, zerolinecolor: grid },
  };
  const merged: Record<string, unknown> = { ...themed, ...overrides };
  for (const ax of ["xaxis", "yaxis"]) {
    if (overrides && overrides[ax]) {
      merged[ax] = {
        ...(themed[ax] as Record<string, unknown>),
        ...(overrides[ax] as Record<string, unknown>),
      };
    }
  }
  return merged;
}

export function Plot({ data, layout, height = 280 }: PlotProps) {
  const ref = useRef<HTMLDivElement>(null);
  // re-render when the app theme flips so charts pick up the new token colors
  const [themeTick, setThemeTick] = useState(0);
  useEffect(() => {
    const obs = new MutationObserver(() => setThemeTick((t) => t + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!ref.current) return;
    Plotly.react(
      ref.current,
      data,
      { ...mergeLayout(layout), height },
      { responsive: true, displaylogo: false },
    );
  }, [data, layout, height, themeTick]);

  useEffect(() => {
    const el = ref.current;
    return () => {
      if (el) Plotly.purge(el);
    };
  }, []);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
