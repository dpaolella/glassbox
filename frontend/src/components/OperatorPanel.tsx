import { useEffect, useState } from "react";
import { api, ExplainPayload } from "../api";

interface Props {
  layer: string;
}

type OpKind = "attribute" | "spatial" | "temporal";

function ExplainView({ payload }: { payload: ExplainPayload }) {
  return (
    <div className="explain">
      <h4>{payload.title}</h4>
      {payload.formulation.statement && (
        <p className="formulation">{payload.formulation.statement}</p>
      )}
      {payload.formulation.symbolic.length > 0 && (
        <pre className="symbolic">
          {payload.formulation.symbolic.join("\n")}
        </pre>
      )}
      {payload.map && (
        <p className="muted">
          map: {payload.map.kind} · {payload.map.n_periods} representative
          timesteps
        </p>
      )}
      {payload.information_loss.length > 0 && (
        <div className="loss-box">
          <div className="loss-title">information loss</div>
          <ul>
            {payload.information_loss.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </div>
      )}
      <details>
        <summary>inputs / outputs / intermediates</summary>
        <pre className="json">
          {JSON.stringify(
            {
              inputs: payload.inputs,
              outputs: payload.outputs,
              intermediates: payload.intermediates,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}

export function OperatorPanel({ layer }: Props) {
  const [op, setOp] = useState<OpKind>("attribute");
  const [spatialMode, setSpatialMode] = useState("aggregate");
  const [temporalKind, setTemporalKind] = useState("representative_days");
  const [payload, setPayload] = useState<ExplainPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    setPayload(null);
    const p =
      op === "attribute"
        ? api.attributeExplain(layer)
        : op === "spatial"
          ? api.spatialExplain(spatialMode)
          : api.temporalExplain(temporalKind);
    p.then(setPayload).catch((e) => setErr(String(e)));
  }, [op, layer, spatialMode, temporalKind]);

  return (
    <div className="operator-panel">
      <p className="muted">
        Every projection operator implements <code>explain()</code> and surfaces
        where it loses information (Section 5 / 2.2).
      </p>
      <div className="op-tabs">
        {(["attribute", "spatial", "temporal"] as OpKind[]).map((k) => (
          <button
            key={k}
            className={`mini-tab ${op === k ? "active" : ""}`}
            onClick={() => setOp(k)}
          >
            {k}
          </button>
        ))}
      </div>

      {op === "attribute" && (
        <p className="muted">
          Projecting on the current layer: <b>{layer}</b>
        </p>
      )}
      {op === "spatial" && (
        <select value={spatialMode} onChange={(e) => setSpatialMode(e.target.value)}>
          <option value="identity">identity (nodal)</option>
          <option value="aggregate">aggregate (zonal)</option>
          <option value="elaborate">elaborate (EMT 3-phase)</option>
        </select>
      )}
      {op === "temporal" && (
        <select
          value={temporalKind}
          onChange={(e) => setTemporalKind(e.target.value)}
        >
          <option value="full_chronology">full chronology</option>
          <option value="representative_days">representative days</option>
        </select>
      )}

      {err && <div className="error-banner">{err}</div>}
      {payload && <ExplainView payload={payload} />}
    </div>
  );
}
