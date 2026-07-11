import { useMemo, useState } from "react";
import { FIELD_GLOSSARY, GLOSSARY } from "../glossary";

// A browsable, searchable glossary (issue #21). The same definitions that power
// the hover tooltips, surfaced as a reference page — discoverable, and usable
// on touch devices where title-attribute tooltips don't exist.

const SECTIONS: [string, Record<string, string>][] = [
  ["Concepts & map", GLOSSARY],
  ["Entity fields (inspector)", FIELD_GLOSSARY],
];

function label(key: string): string {
  return key.replace(/_/g, " ");
}

export function GlossaryPanel() {
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return SECTIONS.map(([title, dict]) => {
      const entries = Object.entries(dict)
        .filter(
          ([k, v]) =>
            !needle ||
            k.toLowerCase().includes(needle) ||
            v.toLowerCase().includes(needle),
        )
        .sort(([a], [b]) => a.localeCompare(b));
      return [title, entries] as const;
    }).filter(([, entries]) => entries.length > 0);
  }, [q]);

  return (
    <div className="glossary-panel">
      <p className="muted">
        Every term used across the map, inspector, and scenario lab. These same
        definitions appear as hover tooltips throughout the app.
      </p>
      <input
        className="glossary-search"
        placeholder="search terms… (e.g. LMP, p_min_pu, curtailment)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {filtered.length === 0 && <p className="muted">no matches.</p>}
      {filtered.map(([title, entries]) => (
        <div key={title}>
          <div className="catalog-section">{title}</div>
          <dl className="glossary-list">
            {entries.map(([k, v]) => (
              <div key={k} className="glossary-entry">
                <dt>{label(k)}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}
