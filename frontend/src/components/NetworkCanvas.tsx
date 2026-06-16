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

function CanvasInner({ layer, selection, onSelect }: Props) {
  const [graph, setGraph] = useState<GraphData | null>(null);

  useEffect(() => {
    api.graph().then(setGraph);
  }, []);

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
                {nGen > 0 && <span title="generators">⚡{nGen}</span>}
                {nStore > 0 && <span title="storage">🔋{nStore}</span>}
                {nLoad > 0 && <span title="loads">🏠{nLoad}</span>}
                {b.bus_type === "slack" && <span title="slack bus">★</span>}
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

    const edges: Edge[] = graph.edges.map((e) => {
      const dashed = e.kind === "dc_line";
      const candidate = e.is_candidate;
      const weak = (e.x ?? 0) >= 0.25;
      return {
        id: e.id,
        source: e.from,
        target: e.to,
        animated: e.kind === "dc_line",
        label:
          e.kind === "transformer" ? "T" : e.kind === "dc_line" ? "DC" : undefined,
        style: {
          stroke: candidate ? "#f59e0b" : weak ? "#ef4444" : "#475569",
          strokeWidth: candidate ? 2 : 1.5,
          strokeDasharray: dashed || candidate ? "6 3" : undefined,
        },
      };
    });

    return { nodes, edges };
  }, [graph, selection]);

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
      <div className="canvas-legend">
        <div className="legend-title">layer: {layer}</div>
        {graph?.zones.map((z) => (
          <div key={z.id} className="legend-row">
            <span className="swatch" style={{ background: zoneColor(z.id) }} />
            {z.name}
          </div>
        ))}
        <div className="legend-row">
          <span className="swatch line" style={{ background: "#f59e0b" }} />
          candidate (CEM)
        </div>
        <div className="legend-row">
          <span className="swatch line" style={{ background: "#ef4444" }} />
          weak feeder (low SCR)
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
