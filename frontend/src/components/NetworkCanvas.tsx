import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Edge,
  Node,
  ReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";
import { api, GraphData } from "../api";
import { Selection } from "../App";
import { GLOSSARY } from "../glossary";

const ZONE_COLORS: Record<string, string> = {
  ZA: "#3b82f6",
  ZB: "#22c55e",
  ZC: "#a855f7",
};

function zoneColor(zone: string): string {
  return ZONE_COLORS[zone] ?? "#64748b";
}

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
  load: boolean;
  interfaces: boolean;
}

const DEFAULT_OVERLAYS: Overlays = {
  ac_line: true,
  transformer: true,
  dc_line: true,
  gen: true,
  storage: true,
  load: true,
  interfaces: false,
};

function CanvasInner({ layer, selection, onSelect }: Props) {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [ov, setOv] = useState<Overlays>(DEFAULT_OVERLAYS);

  useEffect(() => {
    api.graph().then(setGraph);
  }, []);

  // lines that belong to a binding interface (for the interface overlay)
  const interfaceLines = useMemo(() => {
    const m = new Set<string>();
    graph?.interfaces.forEach((i) => i.member_line_ids.forEach((l) => m.add(l)));
    return m;
  }, [graph]);

  const { nodes, edges } = useMemo(() => {
    if (!graph) return { nodes: [] as Node[], edges: [] as Edge[] };

    const nodes: Node[] = graph.nodes.map((b) => {
      const nGen = b.attached.generators.length;
      const nStore = b.attached.storage.length;
      const nLoad = b.attached.loads.length;
      const selected =
        selection?.collection === "buses" && selection.id === b.id;
      return {
        id: b.id,
        position: { x: b.x, y: b.y },
        data: {
          label: (
            <div className="bus-node-label">
              <strong>{b.id}</strong>
              <div className="bus-badges">
                {ov.gen && nGen > 0 && (
                  <span title={GLOSSARY.generators}>⚡{nGen}</span>
                )}
                {ov.storage && nStore > 0 && (
                  <span title={GLOSSARY.storage}>🔋{nStore}</span>
                )}
                {ov.load && nLoad > 0 && (
                  <span title={GLOSSARY.loads}>🏠{nLoad}</span>
                )}
                {b.bus_type === "slack" && (
                  <span title={GLOSSARY.slack}>★</span>
                )}
              </div>
            </div>
          ),
        },
        style: {
          background: "#141b26",
          border: `2px solid ${zoneColor(b.zone)}`,
          borderRadius: 10,
          color: "#e6edf6",
          width: 76,
          fontSize: 11,
          boxShadow: selected ? `0 0 0 3px ${zoneColor(b.zone)}88` : "none",
        },
      };
    });

    const edges: Edge[] = graph.edges
      .filter((e) => ov[e.kind as keyof Overlays])
      .map((e) => {
        const dashed = e.kind === "dc_line";
        const candidate = e.is_candidate;
        const weak = (e.x ?? 0) >= 0.25;
        const onInterface = ov.interfaces && interfaceLines.has(e.id);
        let stroke = candidate ? "#f59e0b" : weak ? "#ef4444" : "#475569";
        if (onInterface) stroke = "#38bdf8";
        return {
          id: e.id,
          source: e.from,
          target: e.to,
          animated: e.kind === "dc_line" || onInterface,
          label:
            e.kind === "transformer" ? "T" : e.kind === "dc_line" ? "DC" : undefined,
          style: {
            stroke,
            strokeWidth: onInterface ? 3 : candidate ? 2 : 1.5,
            strokeDasharray: dashed || candidate ? "6 3" : undefined,
          },
        };
      });

    return { nodes, edges };
  }, [graph, selection, ov, interfaceLines]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      fitView
      minZoom={0.2}
      onNodeClick={(_, node) => onSelect({ collection: "buses", id: node.id })}
      onEdgeClick={(_, edge) => {
        const e = graph?.edges.find((x) => x.id === edge.id);
        const coll =
          e?.kind === "dc_line"
            ? "dc_lines"
            : e?.kind === "transformer"
              ? "transformers"
              : "ac_lines";
        onSelect({ collection: coll, id: edge.id });
      }}
      proOptions={{ hideAttribution: true }}
    >
      <Background color="#1e2733" gap={24} />
      <Controls />
      <div className="canvas-overlays">
        <div className="legend-title" title="Toggle which layers are drawn on the map">
          overlays
        </div>
        {(
          [
            ["ac_line", "AC lines", GLOSSARY.ac_line],
            ["transformer", "transformers", GLOSSARY.transformer],
            ["dc_line", "DC links", GLOSSARY.dc_line],
            ["gen", "generators", GLOSSARY.generators],
            ["storage", "storage", GLOSSARY.storage],
            ["load", "loads", GLOSSARY.loads],
            ["interfaces", "interfaces (flowgates)", GLOSSARY.interface],
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
        <div className="legend-title" title={GLOSSARY.zone}>
          zones (bus outline color)
        </div>
        {graph?.zones.map((z) => (
          <div key={z.id} className="legend-row" title={`${z.name} — ${GLOSSARY.zone}`}>
            <span className="swatch" style={{ background: zoneColor(z.id) }} />
            {z.name}
          </div>
        ))}
        <div className="legend-title" style={{ marginTop: 8 }}>
          on each bus
        </div>
        <div className="legend-row">
          <span title={GLOSSARY.generators}>⚡ generators</span> &nbsp;
          <span title={GLOSSARY.storage}>🔋 storage</span>
        </div>
        <div className="legend-row">
          <span title={GLOSSARY.loads}>🏠 loads</span> &nbsp;
          <span title={GLOSSARY.slack}>★ slack bus</span>
        </div>
        <div className="legend-title" style={{ marginTop: 8 }}>lines</div>
        <div className="legend-row" title={GLOSSARY.candidate}>
          <span className="swatch line" style={{ background: "#f59e0b" }} />
          candidate (CEM build option)
        </div>
        <div className="legend-row" title={GLOSSARY.weak_feeder}>
          <span className="swatch line" style={{ background: "#ef4444" }} />
          weak feeder (low SCR)
        </div>
        <div className="legend-hint">
          hover any item for a definition · click a bus → its devices appear in
          the inspector ({layer} layer)
        </div>
      </div>
    </ReactFlow>
  );
}

export function NetworkCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}
