// The fitness plan's weekly check-in card, and the mid-week fatigue question
// both plans share. The card opens on the last day of the week ("Plan next
// week"): last week's numbers, form, and the four choices with one marked
// Recommended. Answered or applied automatically, it folds to one line with a
// Change button for as long as the choice can still be changed.
import { useEffect, useState } from "react";
import { C } from "../theme";
import { api } from "../api";
import { dayMon } from "../charts/PlanBars";
import { localISO } from "../derive";
import type { SeasonBand, SeasonChoice, SeasonPlan } from "../types";

const LABEL: Record<SeasonChoice, string> = {
  increase: "Increase",
  stay: "Stay",
  decrease: "Decrease",
  recovery: "Recovery",
};

// Band chips carry their name as text, so the colour is never the only cue.
const BAND: Record<SeasonBand, { label: string; color: string }> = {
  fresh: { label: "Fresh", color: C.form },
  ok: { label: "OK", color: C.muted2 },
  tired: { label: "Tired", color: C.fat },
  high: { label: "High", color: C.red },
};

function signed(n: number): string {
  return `${n > 0 ? "+" : ""}${n}%`;
}

export function CheckinCard({ plan, onChanged }: { plan: SeasonPlan; onChanged: () => void }) {
  const ci = plan.checkins[plan.checkins.length - 1];
  const week = ci ? plan.weeks.find((w) => w.week_start === ci.week_start) : undefined;
  // A stored answer stays changeable while its week is next or current.
  const live = !!ci && (ci.status === "pending" || week?.status === "current" || week?.status === "next");
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [choice, setChoice] = useState<SeasonChoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A new check-in starts on its recommendation.
  useEffect(() => {
    setChoice(ci ? ci.choice ?? ci.recommended : null);
    setError(null);
    setOpen(false);
  }, [ci?.week_start, ci?.status, ci?.choice]);

  if (!ci || !live) return null;

  async function submit() {
    if (!ci || !choice || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.answerCheckin(ci.week_start, choice);
      setOpen(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // The week being decided: next week from the last day of this one, or this
  // week itself when it started before a sync decided it.
  const startedAlready = ci.week_start <= localISO(new Date());
  const which = week?.status === "current" || startedAlready ? "This week" : "Next week";
  if (ci.status !== "pending" && !open) {
    const o = ci.options.find((x) => x.choice === ci.choice);
    return (
      <div style={foldStyle}>
        <span style={{ fontSize: 12, color: C.textSoft }}>
          {which}: <b style={{ color: C.text }}>{ci.choice ? LABEL[ci.choice] : ""}</b>
          {o ? `, ${o.target_tss} TSS` : ""}
          {ci.status === "auto_applied" ? " (set automatically)" : ""}
        </span>
        <SmallLink onClick={() => setOpen(true)}>Change</SmallLink>
      </div>
    );
  }
  if (ci.status === "pending" && dismissed) {
    return (
      <div style={foldStyle}>
        <span style={{ fontSize: 12, color: C.textSoft }}>{which}'s check-in is open.</span>
        <SmallLink onClick={() => setDismissed(false)}>Plan {which.toLowerCase()}</SmallLink>
      </div>
    );
  }

  const cw = ci.closing_week;
  const band = BAND[ci.form.band];
  const pending = ci.status === "pending";

  return (
    <div
      style={{ margin: "6px 13px 4px", background: C.inner, border: `1px solid ${C.cardBorder}`, borderRadius: 3, padding: "11px 12px", display: "flex", flexDirection: "column", gap: 10 }}
      onKeyDown={(e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          submit();
        }
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: C.text }}>
          {pending ? `Plan ${which.toLowerCase()}` : `Change ${which.toLowerCase()}`}
        </span>
        <span style={{ fontSize: 11, color: C.muted2 }}>week of {dayMon(ci.week_start)}</span>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "stretch" }}>
        {cw && (
          <Box title={`Last week, from ${dayMon(cw.week_start)}`}>
            <span>Target {cw.target_tss} TSS</span>
            <span>
              {pending && cw.planned_remaining_tss > 0 ? `Projected ${cw.projected_tss}` : `Done ${cw.projected_tss}`} ({cw.completion_pct}%)
            </span>
          </Box>
        )}
        <Box title="Form">
          <span>
            CTL {ci.form.ctl != null ? Math.round(ci.form.ctl) : "-"} · ATL {ci.form.atl != null ? Math.round(ci.form.atl) : "-"} · TSB{" "}
            {ci.form.tsb != null ? Math.round(ci.form.tsb) : "-"}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: band.color }} />
            Fatigue: {band.label}
          </span>
        </Box>

        <div role="radiogroup" aria-label="Next week's load" style={{ display: "flex", gap: 8, flex: 1, minWidth: 420 }}>
          {ci.options.map((o) => {
            const on = choice === o.choice;
            const recommended = o.choice === ci.recommended;
            return (
              <button
                key={o.choice}
                role="radio"
                aria-checked={on}
                disabled={!o.enabled || busy}
                title={o.disabled_reason ?? undefined}
                onClick={() => setChoice(o.choice)}
                style={{
                  flex: 1,
                  minHeight: 32,
                  textAlign: "left",
                  cursor: o.enabled ? "pointer" : "not-allowed",
                  padding: "8px 10px",
                  borderRadius: 4,
                  background: on ? C.inner2 : C.card,
                  border: `1px solid ${on ? C.form : C.cardBorder}`,
                  opacity: o.enabled ? 1 : 0.45,
                  display: "flex",
                  flexDirection: "column",
                  gap: 3,
                }}
              >
                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 11.5, fontWeight: 600, color: C.text }}>{LABEL[o.choice]}</span>
                  {recommended && <span style={chipStyle}>Recommended</span>}
                </span>
                <span style={{ fontSize: 18, fontWeight: 700, color: C.text, lineHeight: 1.1 }}>{o.target_tss}</span>
                <span style={{ fontSize: 10.5, color: C.muted3 }}>
                  TSS · {o.target_hours.toFixed(1)} h · {signed(o.delta_pct_vs_last)}
                </span>
                {o.choice === "recovery" && ci.return_week && <span style={{ fontSize: 10, color: C.muted2 }}>a return week</span>}
                {!o.enabled && o.disabled_reason && <span style={{ fontSize: 10, color: C.muted2, lineHeight: 1.3 }}>{o.disabled_reason}</span>}
              </button>
            );
          })}
        </div>
      </div>

      <span style={{ fontSize: 11.5, color: C.textSoft }}>{ci.reason}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 10.5, color: C.muted2, flex: 1 }}>
          {pending
            ? startedAlready
              ? `If you don't choose, the next sync sets ${LABEL[ci.recommended]}. You can change it any time this week.`
              : `If you don't choose, ${LABEL[ci.recommended]} is set when the week starts. You can change it any time that week.`
            : "The week's target follows from last week's final total."}
        </span>
        {error && <span style={{ fontSize: 11, color: C.red }}>{error}</span>}
        {pending ? (
          <SmallLink onClick={() => setDismissed(true)}>Skip for now</SmallLink>
        ) : (
          <SmallLink onClick={() => setOpen(false)}>Keep it</SmallLink>
        )}
        <button
          onClick={submit}
          disabled={busy || !choice}
          style={{ cursor: "pointer", fontSize: 12, fontWeight: 600, padding: "7px 14px", borderRadius: 6, border: "none", background: C.form, color: "#fff", opacity: busy || !choice ? 0.5 : 1 }}
        >
          {busy ? "Saving..." : `Set ${which.toLowerCase()}`}
        </button>
      </div>
    </div>
  );
}

// "Fatigue is high. Make the rest of this week easy?" -- asked once a week.
export function FatigueBanner({ plan, onChanged }: { plan: SeasonPlan; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const banner = plan.fatigue_banner;
  if (!banner) return null;

  async function answer(accept: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api.weekEasy(accept);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ margin: "8px 13px 0", padding: "9px 12px", background: "#2a2214", border: `1px solid ${C.cardBorder}`, borderRadius: 3, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
      <span style={{ fontSize: 12, color: C.textSoft, flex: 1 }}>
        Fatigue is high (TSB {banner.tsb}). Make the rest of this week easy?
      </span>
      {error && <span style={{ fontSize: 11, color: C.red }}>{error}</span>}
      <SmallLink onClick={() => answer(false)} disabled={busy}>Not now</SmallLink>
      <button
        onClick={() => answer(true)}
        disabled={busy}
        style={{ cursor: "pointer", fontSize: 12, fontWeight: 600, padding: "7px 14px", borderRadius: 6, border: "none", background: C.fat, color: "#1a1206", opacity: busy ? 0.5 : 1 }}
      >
        Yes, make it easy
      </button>
    </div>
  );
}

function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.cardBorder}`, borderRadius: 4, padding: "8px 10px", display: "flex", flexDirection: "column", gap: 3, fontSize: 11.5, color: C.textSoft, minWidth: 150 }}>
      <span style={{ fontSize: 10, letterSpacing: ".06em", textTransform: "uppercase", color: C.muted }}>{title}</span>
      {children}
    </div>
  );
}

function SmallLink({ children, onClick, disabled }: { children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{ background: "none", border: "none", padding: 0, fontSize: 11.5, cursor: "pointer", color: C.muted3, textDecoration: "underline", textUnderlineOffset: 3 }}>
      {children}
    </button>
  );
}

const foldStyle: React.CSSProperties = {
  margin: "6px 13px 2px",
  padding: "6px 10px",
  background: C.inner,
  border: `1px solid ${C.cardBorder}`,
  borderRadius: 3,
  display: "flex",
  alignItems: "center",
  gap: 12,
};

const chipStyle: React.CSSProperties = {
  fontSize: 9,
  fontWeight: 600,
  letterSpacing: ".04em",
  textTransform: "uppercase",
  color: C.form,
  border: `1px solid ${C.form}`,
  borderRadius: 3,
  padding: "0 4px",
};
