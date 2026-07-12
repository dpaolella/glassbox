import { useEffect, useState } from "react";
import { api, InspectField, InspectPayload } from "../api";
import { Selection } from "../App";
import { FIELD_GLOSSARY } from "../glossary";
import { DrillPanel } from "./DrillPanel";

// collections whose fields can be edited in place (issue #28 v2); the
// backend enforces the same list and re-validates every patch with Pydantic
const EDITABLE = new Set([
  "generators", "storage_units", "hydro_units", "loads", "ac_lines",
  "expansion_candidates", "resource_potentials", "fuels", "policies",
  "interfaces", "reserve_products",
]);

// broadcast that the world changed so the map refetches its graph
export function worldEdited() {
  window.dispatchEvent(new Event("gb-world-edited"));
}

function EditableValue({
  collection,
  entityId,
  field,
  display,
  onSaved,
  onError,
}: {
  collection: string;
  entityId: string;
  field: InspectField;
  display: string;
  onSaved: () => void;
  onError: (msg: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const editableType =
    typeof field.value === "number" ||
    typeof field.value === "string" ||
    typeof field.value === "boolean" ||
    field.value === null;
  if (!editableType || field.name === "id") {
    return <>{display}</>;
  }

  function commit() {
    const raw = draft.trim();
    let value: unknown = raw;
    if (typeof field.value === "number" || field.value === null) {
      if (raw === "") value = null;
      else {
        const n = Number(raw);
        if (Number.isNaN(n)) {
          onError(`"${raw}" is not a number`);
          setEditing(false);
          return;
        }
        value = n;
      }
    } else if (typeof field.value === "boolean") {
      value = raw === "true" || raw === "1";
    }
    setBusy(true);
    api
      .patchEntity(collection, entityId, { [field.name]: value })
      .then(() => {
        onSaved();
        worldEdited();
      })
      .catch((e) => onError(String(e.message ?? e)))
      .finally(() => {
        setBusy(false);
        setEditing(false);
      });
  }

  if (editing) {
    return (
      <input
        className="edit-input"
        autoFocus
        defaultValue={field.value === null ? "" : String(field.value)}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(false);
        }}
        onBlur={() => setEditing(false)}
      />
    );
  }
  return (
    <span
      className="editable-value"
      title="click to edit (validated server-side; undoable from the build palette)"
      onClick={() => {
        setDraft(field.value === null ? "" : String(field.value));
        setEditing(true);
      }}
    >
      {busy ? "…" : display}
      <span className="edit-pencil">✎</span>
    </span>
  );
}

interface Props {
  selection: Selection | null;
  layer: string;
  perUnit: boolean;
  onSelect: (sel: Selection) => void;
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString();
    if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString();
    return String(Number(v.toFixed(4)));
  }
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? `[${v.length}]` : "[]";
  if (typeof v === "object") return JSON.stringify(v);
  return fmt(v);
}

// short column headers for nested object-array tables (e.g. supply-curve tranches)
const COL_LABEL: Record<string, string> = {
  build_max_mw: "MW",
  capex_per_mw: "$/MW",
  capex_per_mwh: "$/MWh(e)",
  fom_per_mw_yr: "FOM $/MW-yr",
  expected_capacity_factor: "CF",
  lcoe_per_mwh: "LCOE $/MWh",
  breakpoint_mw: "MW",
  marginal_cost_per_mwh: "$/MWh",
};

// Is this an array of plain objects (a table), e.g. tranches / cost-curve segments?
function isObjectArray(v: unknown): v is Record<string, unknown>[] {
  return (
    Array.isArray(v) &&
    v.length > 0 &&
    v.every((x) => x !== null && typeof x === "object" && !Array.isArray(x))
  );
}

function NestedTable({ rows }: { rows: Record<string, unknown>[] }) {
  // union of keys in first-row order, dropping columns null in every row
  const keys: string[] = [];
  for (const r of rows) for (const k of Object.keys(r)) if (!keys.includes(k)) keys.push(k);
  const cols = keys.filter((k) => rows.some((r) => r[k] !== null && r[k] !== undefined));
  return (
    <table className="nested-table">
      <thead>
        <tr>
          <th>#</th>
          {cols.map((c) => (
            <th key={c} title={FIELD_GLOSSARY[c] ?? c}>
              {COL_LABEL[c] ?? c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td className="muted">{i + 1}</td>
            {cols.map((c) => (
              <td key={c}>{fmt(r[c])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Inspector({ selection, layer, perUnit, onSelect }: Props) {
  const [payload, setPayload] = useState<InspectPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [editMsg, setEditMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!selection) {
      setPayload(null);
      return;
    }
    setErr(null);
    api
      .inspect(selection.collection, selection.id, layer)
      .then(setPayload)
      .catch((e) => setErr(String(e)));
  }, [selection, layer, refresh]);
  useEffect(() => setEditMsg(null), [selection]);

  const editable = selection ? EDITABLE.has(selection.collection) : false;

  function retire() {
    if (!selection) return;
    api.patchEntity(selection.collection, selection.id, { in_service: false })
      .then(() => { setEditMsg("retired (in_service = false) — undoable"); setRefresh((r) => r + 1); worldEdited(); })
      .catch((e) => setEditMsg(String(e.message ?? e)));
  }

  function remove() {
    if (!selection) return;
    api.deleteEntity(selection.collection, selection.id)
      .then(() => { setEditMsg(`deleted ${selection.id} — undoable from the build palette`); setPayload(null); worldEdited(); })
      .catch((e) => setEditMsg(String(e.message ?? e)));
  }

  if (!selection)
    return (
      <div className="empty-hint">
        <p>Click a bus or line on the map to inspect it.</p>
        <p className="muted">
          Generators, storage, hydro and loads attach to buses — click a bus,
          then click an attached device below its header to inspect it. The
          inspector shows only the fields the current <b>{layer}</b> layer
          consumes — the same object reveals different depth per layer (Section
          9.2). Toggle SI / per-unit in the header.
        </p>
      </div>
    );

  if (err) return <div className="error-banner">{err}</div>;
  if (!payload) return <div className="empty-hint">loading…</div>;

  return (
    <div className="inspector">
      <div className="inspector-head">
        <span className="badge">{payload.type}</span>
        <h3>{payload.id}</h3>
        <span className="muted">
          {payload.fields.length} fields in <b>{layer}</b> scope
        </span>
        {editable && (
          <span className="edit-actions">
            {payload.fields.some((f) => f.name === "in_service") && (
              <button className="edit-btn" title="Take this asset out of service (every engine honors it) — undoable" onClick={retire}>
                ⏻ retire
              </button>
            )}
            <button className="edit-btn danger" title="Remove this entity from the world — undoable from the build palette" onClick={remove}>
              🗑 delete
            </button>
          </span>
        )}
      </div>
      {editMsg && <div className="edit-msg">{editMsg}</div>}

      {payload.attached && payload.attached.length > 0 && (
        <div className="attached">
          <div className="attached-label">
            {payload.collection === "buses" ? "attached devices" : "connected"}
          </div>
          <div className="attached-chips">
            {payload.attached.map((a) => (
              <button
                key={`${a.collection}:${a.id}`}
                className="attached-chip"
                title={`${a.kind} — open inspector`}
                onClick={() => onSelect({ collection: a.collection, id: a.id })}
              >
                {a.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <DrillPanel collection={payload.collection} id={payload.id} />

      {payload.fields.length === 0 && (
        <p className="muted">
          This entity exposes no fields to the <b>{layer}</b> layer — it is
          invisible at this abstraction level.
        </p>
      )}

      <table className="field-table">
        <tbody>
          {payload.fields.map((f) => {
            const showPu = perUnit && f.per_unit;
            const value = showPu ? f.per_unit!.value : f.value;
            const unit = showPu ? f.per_unit!.unit : f.unit;
            const tip = f.description || FIELD_GLOSSARY[f.name] || "";
            const table = isObjectArray(f.value) ? f.value : null;
            return (
              <tr key={f.name}>
                <td className="field-name">
                  <span className={tip ? "has-tip" : ""} title={tip}>
                    {f.name}
                  </span>
                  <div className="field-facets">
                    {f.facets.map((fc) => (
                      <span
                        key={fc}
                        className={`facet-tag ${fc === layer ? "match" : ""}`}
                      >
                        {fc}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="field-value">
                  {table ? (
                    <NestedTable rows={table} />
                  ) : (
                    <>
                      {editable && !showPu && selection ? (
                        <EditableValue
                          collection={selection.collection}
                          entityId={selection.id}
                          field={f}
                          display={renderValue(value)}
                          onSaved={() => {
                            setEditMsg(`updated ${f.name} — undoable`);
                            setRefresh((r) => r + 1);
                          }}
                          onError={(m) => setEditMsg(m)}
                        />
                      ) : (
                        renderValue(value)
                      )}
                      {unit && <span className="unit"> {unit}</span>}
                      {showPu && f.per_unit!.note && (
                        <div className="pu-note">{f.per_unit!.note}</div>
                      )}
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
