import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";
import { PLOTLY_LAYOUT } from "../theme";

interface PlotProps {
  data: Record<string, unknown>[];
  layout?: Record<string, unknown>;
  height?: number;
}

// merge axis objects so per-chart overrides keep the theme's grid styling
function mergeLayout(overrides?: Record<string, unknown>) {
  const merged: Record<string, unknown> = { ...PLOTLY_LAYOUT, ...overrides };
  for (const ax of ["xaxis", "yaxis"]) {
    if (overrides && overrides[ax]) {
      merged[ax] = {
        ...(PLOTLY_LAYOUT[ax] as Record<string, unknown>),
        ...(overrides[ax] as Record<string, unknown>),
      };
    }
  }
  return merged;
}

export function Plot({ data, layout, height = 280 }: PlotProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    Plotly.react(
      ref.current,
      data,
      { ...mergeLayout(layout), height },
      { responsive: true, displaylogo: false },
    );
  }, [data, layout, height]);

  useEffect(() => {
    const el = ref.current;
    return () => {
      if (el) Plotly.purge(el);
    };
  }, []);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
