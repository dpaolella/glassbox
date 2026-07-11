import { useState } from "react";

// First-run guided tour (issue #21): a five-step walkthrough of the
// one-world/many-views thesis. Dismissible, remembered in localStorage.

export interface TourStep {
  title: string;
  body: string;
  action?: () => void; // applied when the step is shown (switch layer/tab)
}

export function tourDone(): boolean {
  return localStorage.getItem("gb-tour-done") === "1";
}

export function Tour({
  steps,
  onClose,
}: {
  steps: TourStep[];
  onClose: () => void;
}) {
  const [i, setI] = useState(0);
  const step = steps[i];

  function go(next: number) {
    const s = steps[next];
    if (s?.action) s.action();
    setI(next);
  }

  function finish() {
    localStorage.setItem("gb-tour-done", "1");
    onClose();
  }

  return (
    <div className="tour-backdrop">
      <div className="tour-card">
        <div className="tour-step">
          step {i + 1} of {steps.length}
        </div>
        <h3>{step.title}</h3>
        <p>{step.body}</p>
        <div className="tour-actions">
          <button className="tour-skip" onClick={finish}>
            skip tour
          </button>
          <span style={{ flex: 1 }} />
          {i > 0 && (
            <button className="tour-nav" onClick={() => go(i - 1)}>
              back
            </button>
          )}
          {i < steps.length - 1 ? (
            <button className="tour-nav primary" onClick={() => go(i + 1)}>
              next
            </button>
          ) : (
            <button className="tour-nav primary" onClick={finish}>
              start exploring
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
