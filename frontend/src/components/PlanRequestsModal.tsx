import { C, DISCIPLINE_COLOR } from "../theme";
import { monthDay } from "../format";
import { useModalKeys } from "../useModalKeys";
import type { PlanRequest, PlanSession } from "../types";

// Asked before Sync Garmin or Re-sync plan while the athlete has plan changes
// in force (days they asked the coach to change). Keep builds the new plan
// around them; overwrite builds a fresh plan and deletes them; cancel runs
// nothing.
export function PlanRequestsModal({
  requests,
  onAnswer,
}: {
  requests: PlanRequest[];
  onAnswer: (choice: "keep" | "overwrite" | null) => void;
}) {
  const cancel = () => onAnswer(null);
  // Esc cancels, Ctrl+Enter keeps -- the safe answer, which loses nothing.
  useModalKeys(cancel, () => onAnswer("keep"));

  const n = requests.length;

  return (
    <div
      onClick={cancel}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 55, padding: 24 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: 480, maxWidth: "100%", maxHeight: "90vh", display: "flex", flexDirection: "column", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "13px 18px", borderTop: `3px solid ${C.form}`, borderBottom: `1px solid ${C.border}`, background: C.bar }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Keep your plan changes?</span>
          <span onClick={cancel} style={{ cursor: "pointer", fontSize: 18, color: C.muted, lineHeight: 1 }}>✕</span>
        </div>

        <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 12, overflowY: "auto" }}>
          <span style={{ fontSize: 12.5, color: C.textChip, lineHeight: 1.5 }}>
            You changed {n === 1 ? "this day" : `these ${n} days`} with the coach.
          </span>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {requests.map((r) => (
              <div key={r.date} style={{ background: C.inner, border: `1px solid ${C.cardBorder}`, borderRadius: 5, padding: "8px 11px", display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: C.text, minWidth: 64 }}>
                    {r.day_name.slice(0, 3)} {monthDay(localDate(r.date))}
                  </span>
                  <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1 }}>
                    {r.sessions.length ? (
                      r.sessions.map((s, i) => (
                        <span key={i} style={{ fontSize: 12, color: C.textSoft }}>
                          <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: 2, marginRight: 6, background: DISCIPLINE_COLOR[s.discipline] ?? C.muted }} />
                          {sessionLabel(s)}
                        </span>
                      ))
                    ) : (
                      <span style={{ fontSize: 12, color: C.textSoft }}>Day off</span>
                    )}
                  </div>
                </div>
                {r.notes.length > 0 && (
                  <span style={{ fontSize: 11, color: C.muted2, lineHeight: 1.4 }}>{r.notes.join(" ")}</span>
                )}
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "12px 18px", borderTop: `1px solid ${C.border}`, background: C.bar }}>
          <button
            onClick={cancel}
            style={{ cursor: "pointer", fontSize: 12.5, fontWeight: 600, padding: "9px 16px", borderRadius: 6, border: `1px solid ${C.cardBorder}`, background: "transparent", color: C.textChip }}
          >
            Cancel
          </button>
          <button
            onClick={() => onAnswer("overwrite")}
            style={{ cursor: "pointer", fontSize: 12.5, fontWeight: 600, padding: "9px 16px", borderRadius: 6, border: `1px solid ${C.cardBorder}`, background: C.inner2, color: C.text }}
          >
            Overwrite with new plan
          </button>
          <button
            onClick={() => onAnswer("keep")}
            style={{ cursor: "pointer", fontSize: 12.5, fontWeight: 600, padding: "9px 16px", borderRadius: 6, border: "none", background: C.form, color: "#fff" }}
          >
            Keep my changes
          </button>
        </div>
      </div>
    </div>
  );
}

// A plan date as a LOCAL Date: `new Date("2026-10-01")` is UTC midnight, which
// renders as the day before west of UTC.
function localDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function sessionLabel(s: PlanSession): string {
  const title = s.title || s.discipline;
  return s.duration_min ? `${title}, ${Math.round(s.duration_min)} min` : title;
}
