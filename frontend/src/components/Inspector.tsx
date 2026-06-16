import { useEffect, useState } from "react";
import { api, InspectPayload } from "../api";
import { Selection } from "../App";

interface Props {
  selection: Selection | null;
  layer: string;
  perUnit: boolean;
}

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4);
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return v.length ? `[${v.length}]` : "[]";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function Inspector({ selection, layer, perUnit }: Props) {
  const [payload, setPayload] = useState<InspectPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

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
  }, [selection, layer]);

  if (!selection)
    return (
      <div className="empty-hint">
        <p>Click a bus or line on the map to inspect it.</p>
        <p className="muted">
          The inspector shows only the fields the current <b>{layer}</b> layer
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
      </div>

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
            return (
              <tr key={f.name} title={f.description}>
                <td className="field-name">
                  {f.name}
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
                  {renderValue(value)}
                  {unit && <span className="unit"> {unit}</span>}
                  {showPu && f.per_unit!.note && (
                    <div className="pu-note">{f.per_unit!.note}</div>
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
