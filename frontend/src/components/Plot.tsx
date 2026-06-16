import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";

interface PlotProps {
  data: Record<string, unknown>[];
  layout?: Record<string, unknown>;
  height?: number;
}

const DARK_LAYOUT: Record<string, unknown> = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#c9d4e3", size: 11 },
  margin: { l: 48, r: 16, t: 28, b: 40 },
  xaxis: { gridcolor: "#27313f", zerolinecolor: "#3a475a" },
  yaxis: { gridcolor: "#27313f", zerolinecolor: "#3a475a" },
  legend: { orientation: "h", y: -0.2 },
};

export function Plot({ data, layout, height = 280 }: PlotProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    Plotly.react(
      ref.current,
      data,
      { ...DARK_LAYOUT, ...layout, height },
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
