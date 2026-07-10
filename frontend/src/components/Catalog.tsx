import { useEffect, useState } from "react";
import { api } from "../api";
import { Selection } from "../App";
import { GLOSSARY } from "../glossary";

// collection name -> glossary key (for hover captions on group headers)
const GLOSS_KEY: Record<string, string> = {
  buses: "pq_bus",
  zones: "zone",
  ac_lines: "ac_line",
  transformers: "transformer",
  dc_lines: "dc_line",
  shunts: "shunts",
  interfaces: "interface",
  generators: "generators",
  hydro_units: "hydro",
  storage_units: "storage",
  loads: "loads",
  expansion_candidates: "expansion_candidates",
  resource_potentials: "resource_potentials",
  fuels: "fuels",
  cost_curves: "cost_curves",
  policies: "policies",
  reserve_products: "reserve_products",
  system_constraints: "system_constraints",
  disturbances: "disturbances",
};

// Every entity type in the world, browsable. Spatial types are also overlay-able
// on the map; non-spatial types (fuels, policies, cost curves, reserves, system
// constraints, disturbances) live here as tables. Click any entity to inspect
// it. Temporal data (weather years, time series) has its own tabs.

interface Props {
  onSelect: (sel: Selection) => void;
  onOpenTab: (tab: string) => void;
  focus: string | null;
}

const SPATIAL: [string, string][] = [
  ["buses", "Buses"],
  ["zones", "Zones"],
  ["ac_lines", "AC lines"],
  ["transformers", "Transformers"],
  ["dc_lines", "DC links"],
  ["shunts", "Shunts"],
  ["interfaces", "Interfaces (flowgates)"],
  ["generators", "Generators"],
  ["hydro_units", "Hydro"],
  ["storage_units", "Storage"],
  ["loads", "Loads"],
];

const TABLES: [string, string][] = [
  ["expansion_candidates", "Expansion candidates (nodal build options)"],
  ["resource_potentials", "Resource potential (zonal supply curves)"],
  ["fuels", "Fuels"],
  ["cost_curves", "Cost curves"],
  ["policies", "Policies"],
  ["reserve_products", "Reserve products"],
  ["system_constraints", "System constraints"],
  ["disturbances", "Disturbances"],
];

function entityLabel(e: Record<string, unknown>): string {
  const id = String(e.id ?? "?");
  const extra =
    (e.technology as string) ||
    (e.kind as string) ||
    (e.name as string) ||
    "";
  return extra && extra !== id ? `${id} · ${extra}` : id;
}

function CollectionGroup({
  collection,
  label,
  open,
  onSelect,
  onOpenTab,
}: {
  collection: string;
  label: string;
  open: boolean;
  onSelect: (s: Selection) => void;
  onOpenTab: (t: string) => void;
}) {
  const [expanded, setExpanded] = useState(open);
  const [items, setItems] = useState<Record<string, unknown>[] | null>(null);

  useEffect(() => {
    if (expanded && items === null) {
      api.listEntities(collection).then(setItems).catch(() => setItems([]));
    }
  }, [expanded, items, collection]);

  useEffect(() => {
    if (open) setExpanded(true);
  }, [open]);

  return (
    <div className="catalog-group">
      <button
        className="catalog-head"
        title={GLOSSARY[GLOSS_KEY[collection]] ?? label}
        onClick={() => setExpanded(!expanded)}
      >
        <span>{expanded ? "▾" : "▸"}</span>
        <span className="catalog-name">{label}</span>
        {items && <span className="catalog-count">{items.length}</span>}
      </button>
      {expanded && (
        <div className="catalog-items">
          {items === null && <div className="muted">loading…</div>}
          {items && items.length === 0 && (
            <div className="muted">none in this world</div>
          )}
          {items?.map((e) => (
            <button
              key={String(e.id)}
              className="catalog-item"
              onClick={() => {
                onSelect({ collection, id: String(e.id) });
                onOpenTab("inspector");
              }}
            >
              {entityLabel(e)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Catalog({ onSelect, onOpenTab, focus }: Props) {
  return (
    <div className="catalog">
      <p className="muted">
        Every entity in the world. Click one to open it in the inspector
        (layer-filtered). Spatial types can also be toggled as map overlays;
        time-varying data has its own tabs.
      </p>

      <div className="catalog-section">On the map</div>
      {SPATIAL.map(([c, l]) => (
        <CollectionGroup
          key={c}
          collection={c}
          label={l}
          open={focus === c}
          onSelect={onSelect}
          onOpenTab={onOpenTab}
        />
      ))}

      <div className="catalog-section">Tables</div>
      {TABLES.map(([c, l]) => (
        <CollectionGroup
          key={c}
          collection={c}
          label={l}
          open={focus === c}
          onSelect={onSelect}
          onOpenTab={onOpenTab}
        />
      ))}

      <div className="catalog-section">Time-varying (separate views)</div>
      <div className="catalog-links">
        <button className="catalog-link" onClick={() => onOpenTab("weather")}>
          Weather years → Weather tab
        </button>
        <button className="catalog-link" onClick={() => onOpenTab("series")}>
          Time series → Series tab
        </button>
      </div>
    </div>
  );
}
