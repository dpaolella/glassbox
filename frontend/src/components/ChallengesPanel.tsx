import { useEffect, useState } from "react";

// Planner challenges (issue #30): goal-driven scenarios scored by the real
// engines. Losing explains itself as well as winning.

const BASE = "/api";

interface Challenge {
  key: string;
  name: string;
  brief: string;
  grades: string;
}

interface Score {
  key: string;
  score: number;
  medal: string;
  metrics: Record<string, unknown>;
  explanation: string;
}

const MEDAL: Record<string, string> = {
  gold: "🥇", silver: "🥈", bronze: "🥉", none: "—",
};

export function ChallengesPanel() {
  const [challenges, setChallenges] = useState<Challenge[]>([]);
  const [scores, setScores] = useState<Record<string, Score | "loading">>({});
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${BASE}/challenges`).then((r) => r.json()).then(setChallenges)
      .catch((e) => setErr(String(e)));
  }, []);

  function evaluate(key: string) {
    setScores((s) => ({ ...s, [key]: "loading" }));
    fetch(`${BASE}/challenges/${key}/score`, { method: "POST" })
      .then((r) => {
        if (!r.ok) throw new Error(`score -> ${r.status}`);
        return r.json();
      })
      .then((sc: Score) => setScores((s) => ({ ...s, [key]: sc })))
      .catch((e) => {
        setErr(String(e));
        setScores((s) => {
          const { [key]: _drop, ...rest } = s;
          return rest;
        });
      });
  }

  return (
    <div className="challenges">
      <p className="muted">
        You are the planning director. Each challenge grades the <b>current
        world</b> — including any proposals you placed in build mode (INV layer
        → build palette on the map) — by running the real engines against its
        targets. If the default options can't win it, design better ones.
      </p>
      {err && <div className="error-banner">{err}</div>}
      {challenges.map((ch) => {
        const sc = scores[ch.key];
        return (
          <div key={ch.key} className="challenge-card">
            <div className="challenge-head">
              <b>{ch.name}</b>
              {sc && sc !== "loading" && (
                <span className="challenge-medal" title={`score ${sc.score}/100`}>
                  {MEDAL[sc.medal] ?? sc.medal} {sc.score}
                </span>
              )}
            </div>
            <p>{ch.brief}</p>
            <p className="muted" style={{ fontSize: "11px" }}>{ch.grades}</p>
            {sc === "loading" ? (
              <p className="muted">running the engines…</p>
            ) : sc ? (
              <>
                <div className="lesson-box" style={{ fontSize: "12px" }}>
                  {sc.explanation}
                </div>
                <details>
                  <summary className="muted" style={{ fontSize: "11px", cursor: "pointer" }}>
                    metrics
                  </summary>
                  <pre className="json">{JSON.stringify(sc.metrics, null, 2)}</pre>
                </details>
                <button className="preset" onClick={() => evaluate(ch.key)}>
                  ↻ re-evaluate
                </button>
              </>
            ) : (
              <button className="preset" onClick={() => evaluate(ch.key)}>
                ▶ evaluate
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
