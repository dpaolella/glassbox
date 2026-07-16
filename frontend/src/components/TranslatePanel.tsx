import { useEffect, useState } from "react";
import {
  api,
  CoverageHop,
  CoverageManifest,
  SidecarEntrySummary,
  TranslateAvailability,
  TranslateImportResult,
} from "../api";

// Import/export foreign model formats via grid-rosetta (issue #53).
// The point of this panel is not the form — it's the RECEIPTS: every
// translation arrives with its coverage manifest (what was approximated,
// parked, dropped, invented, and which entities still need a human to say
// what they are) and its sidecar (concepts still in transit). A translated
// world is never silently "fine".

const TOTAL_LABELS: [string, string][] = [
  ["approximated", "approximated"],
  ["parked", "parked"],
  ["restored", "restored"],
  ["dropped", "dropped"],
  ["invented", "invented"],
  ["manual_mapping_required", "manual mappings"],
];

function Totals({ m }: { m: CoverageManifest }) {
  return (
    <div className="translate-totals">
      {TOTAL_LABELS.map(([k, label]) => (
        <div
          key={k}
          className="translate-tile"
          style={
            k === "manual_mapping_required" && m.totals[k] > 0
              ? { borderColor: "#f59e0b" }
              : undefined
          }
        >
          <b>{m.totals[k]}</b>
          <span>{label}</span>
        </div>
      ))}
      <div className="translate-tile">
        <b>{m.sidecar_remaining}</b>
        <span>in sidecar</span>
      </div>
    </div>
  );
}

function EventList({
  title,
  rows,
}: {
  title: string;
  rows: Record<string, unknown>[];
}) {
  if (!rows.length) return null;
  return (
    <details className="translate-events">
      <summary>
        {title} <span className="muted">({rows.length})</span>
      </summary>
      <ul>
        {rows.map((r, i) => (
          <li key={i}>
            {Object.entries(r)
              .filter(([k]) => !k.startsWith("_"))
              .map(([k, v]) => `${k}: ${String(v)}`)
              .join(" — ")}
          </li>
        ))}
      </ul>
    </details>
  );
}

function HopCard({ hop }: { hop: CoverageHop }) {
  const translated = Object.entries(hop.translated)
    .map(([k, n]) => `${k}: ${n}`)
    .join(", ");
  return (
    <div className="translate-hop">
      <div className="translate-hop-title">{hop.bridge}</div>
      <div className="muted">translated — {translated || "nothing"}</div>
      <EventList title="approximated" rows={hop.approximated} />
      <EventList title="parked" rows={hop.parked} />
      <EventList title="restored" rows={hop.restored} />
      <EventList title="dropped" rows={hop.dropped} />
      <EventList title="invented" rows={hop.invented} />
      <EventList
        title="manual mappings needed"
        rows={hop.manual_mapping_required}
      />
    </div>
  );
}

function Manifest({
  m,
  sidecar,
}: {
  m: CoverageManifest;
  sidecar: SidecarEntrySummary[];
}) {
  return (
    <div>
      <div className="muted">route: {m.route.join(" | ")}</div>
      <Totals m={m} />
      {m.hops.map((h) => (
        <HopCard key={h.bridge} hop={h} />
      ))}
      {sidecar.length > 0 && (
        <details className="translate-events">
          <summary>
            still in the sidecar{" "}
            <span className="muted">({sidecar.length})</span>
          </summary>
          <ul>
            {sidecar.map((e, i) => (
              <li key={i}>
                {e.concept} · {e.entity_id} — {e.reason}
              </li>
            ))}
          </ul>
          <div className="muted">
            These concepts had no home here; an export to a schema that
            understands them will restore them.
          </div>
        </details>
      )}
    </div>
  );
}

export function TranslatePanel({ onImported }: { onImported: () => void }) {
  const [avail, setAvail] = useState<TranslateAvailability | null>(null);
  const [source, setSource] = useState("case14");
  const [schema, setSchema] = useState("matpower");
  const [hub, setHub] = useState<string>("");
  const [hours, setHours] = useState(168);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TranslateImportResult | null>(null);

  const [exportSchema, setExportSchema] = useState("pypsa");
  const [exportName, setExportName] = useState("exported_model");
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [exportManifest, setExportManifest] =
    useState<CoverageManifest | null>(null);

  useEffect(() => {
    api.translateAvailability().then(setAvail).catch((e) =>
      setAvail({ available: false, reason: String(e) }),
    );
  }, []);

  if (!avail) return <div className="muted">checking translation layer…</div>;
  if (!avail.available)
    return (
      <div>
        <p>Translation is an optional feature and is not installed.</p>
        <p className="muted">{avail.reason}</p>
      </div>
    );

  const doImport = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.translateImport({
        source,
        schema_name: schema,
        hub: hub || null,
        hours,
      });
      setResult(r);
      onImported();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doExport = async () => {
    setBusy(true);
    setError(null);
    setExportMsg(null);
    setExportManifest(null);
    try {
      const r = await api.translateExport({
        schema_name: exportSchema,
        hub: hub || null,
        name: exportName,
        hours,
      });
      setExportMsg(`written to ${r.exported}`);
      setExportManifest(r.manifest);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const schemas = avail.importable_from ?? [];
  const hubs = avail.hubs ?? [];

  return (
    <div className="translate-panel">
      <h3>Import a foreign model</h3>
      <p className="muted">
        Translation via grid-rosetta. Every import arrives with its receipts:
        what the route approximated, parked in the sidecar, dropped, invented,
        and which entities need you to say what they are.
      </p>
      <div className="translate-form">
        <label>
          format
          <select value={schema} onChange={(e) => setSchema(e.target.value)}>
            {schemas.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          source (path or case name)
          <input
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="case14 · /path/to/net.nc · model.xml"
          />
        </label>
        <label>
          via hub
          <select value={hub} onChange={(e) => setHub(e.target.value)}>
            <option value="">direct (no hub)</option>
            {hubs.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
        </label>
        <label>
          hours
          <input
            type="number"
            value={hours}
            min={1}
            onChange={(e) => setHours(Number(e.target.value))}
          />
        </label>
        <button disabled={busy} onClick={doImport}>
          {busy ? "translating…" : "import as live world"}
        </button>
      </div>

      {error && <div className="translate-error">{error}</div>}

      {result && (
        <div>
          <h4>
            imported: {result.world.name}{" "}
            <span className="muted">
              (
              {Object.entries(result.world.counts)
                .filter(([, n]) => n > 0)
                .map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`)
                .slice(0, 5)
                .join(", ")}
              )
            </span>
          </h4>
          <Manifest m={result.manifest} sidecar={result.sidecar} />
        </div>
      )}

      <h3>Export the live world</h3>
      <div className="translate-form">
        <label>
          format
          <select
            value={exportSchema}
            onChange={(e) => setExportSchema(e.target.value)}
          >
            {(avail.schemas ?? [])
              .filter((s) => s !== "glassbox" && s !== "plexos")
              .map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
          </select>
        </label>
        <label>
          name
          <input
            value={exportName}
            onChange={(e) => setExportName(e.target.value)}
          />
        </label>
        <button disabled={busy} onClick={doExport}>
          {busy ? "translating…" : "export"}
        </button>
      </div>
      {exportMsg && <div className="muted">{exportMsg}</div>}
      {exportManifest && <Manifest m={exportManifest} sidecar={[]} />}
    </div>
  );
}
