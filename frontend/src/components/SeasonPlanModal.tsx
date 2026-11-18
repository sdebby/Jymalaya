// Create a season plan, in three steps: the goal (which event), the settings
// (hours, build-up pace, peak fitness, start week) and a preview -- four when
// the event has earlier racing events, which can be made sub events (the plan
// eases off around them, the chosen event stays the goal). Then a preview of exactly the
// chart and two lines the panel will show. The preview is the backend's own
// plan (POST /season-plan/preview, no side effects), refreshed as the settings
// change, so what the athlete approves is what gets saved.
import { useEffect, useMemo, useState } from "react";
import { C } from "../theme";
import { api } from "../api";
import { useModalKeys } from "../useModalKeys";
import { localISO } from "../derive";
import { draftedRows, SplitTable, tableOf, type SplitDraft } from "./SeasonSplit";
import { PlanBars, dayMon } from "../charts/PlanBars";
import { EVENT_TYPES } from "../types";
import type { RaceEvent, SeasonFocus, SeasonInstructionsView, SeasonPlan, SeasonPlanIn, SeasonRamp } from "../types";

const RAMPS: { v: SeasonRamp; label: string; help: string }[] = [
  { v: "gentle", label: "Gentle", help: "+5% a week. For a comeback, or a busy season." },
  { v: "standard", label: "Standard", help: "+8% a week. The usual build." },
  { v: "ambitious", label: "Ambitious", help: "+10% a week, the most the plan ever adds." },
];

type StepName = "Goal" | "Sub events" | "Settings" | "Preview";

const FOCUSES: { v: SeasonFocus; label: string; help: string }[] = [
  { v: "triathlon", label: "Triathlon", help: "Swim, bike and run." },
  { v: "run", label: "Run", help: "Running only." },
  { v: "bike", label: "Bike", help: "Cycling only." },
  { v: "swim", label: "Swim", help: "Swimming only." },
];

function typeLabel(type: string): string {
  return EVENT_TYPES.find((t) => t.value === type)?.label ?? type;
}

function weeksAway(raceDate: string): number {
  const [y, m, d] = raceDate.split("-").map(Number);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.max(0, Math.round((new Date(y, m - 1, d).getTime() - today) / (7 * 86400000)));
}

export function SeasonPlanModal({
  events,
  initialEventId,
  initialMode = "event",
  convert = false,
  weekStart,
  onClose,
  onCreated,
}: {
  // Events the athlete is racing; only upcoming ones can carry a plan.
  events: RaceEvent[];
  initialEventId?: string;
  initialMode?: "event" | "fitness";
  // Turn the current plan (its event gone) into a fitness plan, keeping its
  // finished weeks.
  convert?: boolean;
  // Settings > Display "Week starts on"; the plan keeps the one it was made with.
  weekStart: 0 | 1;
  onClose: () => void;
  onCreated: () => void;
}) {
  const upcoming = useMemo(() => {
    const today = localISO(new Date());
    return events.filter((e) => (e.race_date || "") >= today).sort((a, b) => a.race_date.localeCompare(b.race_date));
  }, [events]);

  const [step, setStep] = useState(convert ? 1 : 0);
  const [mode, setMode] = useState<"event" | "fitness">(convert ? "fitness" : initialMode);
  const [focus, setFocus] = useState<SeasonFocus>("triathlon");
  const fitness = mode === "fitness";
  const [eventId, setEventId] = useState<string | undefined>(
    upcoming.find((e) => e.event_id === initialEventId)?.event_id ?? upcoming[0]?.event_id,
  );
  const [hours, setHours] = useState("");
  // Null until picked: the coach instructions (or the default) decide.
  const [ramp, setRamp] = useState<SeasonRamp | null>(null);
  const [useInstructions, setUseInstructions] = useState(true);
  // Blank = the distance's default, which the preview reports back.
  const [peakCtl, setPeakCtl] = useState("");
  const [start, setStart] = useState<"this" | "next" | null>(null);
  // The profile's activity class: it picks the pace, recovery spacing and peak
  // the form leaves blank, below the coach instructions. Only named in the help.
  const [activityClass, setActivityClass] = useState<number | null>(null);
  useEffect(() => {
    api.athlete().then((p) => setActivityClass(p.activity_class ?? null)).catch(() => {});
  }, []);
  const [preview, setPreview] = useState<SeasonPlan | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Triathlon only: the split table rows the athlete changed; another race or
  // focus starts from its own defaults again.
  // Sub events: every earlier racing event is ticked until unticked here.
  const mainEvent = upcoming.find((e) => e.event_id === eventId);
  const candidates = fitness || !mainEvent ? [] : upcoming.filter((e) => e.event_id !== eventId && e.race_date < mainEvent.race_date);
  const [subOff, setSubOff] = useState<string[]>([]);
  const subIds = candidates.filter((e) => !subOff.includes(e.event_id)).map((e) => e.event_id);
  const steps: StepName[] = candidates.length ? ["Goal", "Sub events", "Settings", "Preview"] : ["Goal", "Settings", "Preview"];
  const stepName = steps[Math.min(step, steps.length - 1)];
  const lastStep = stepName === "Preview";
  const [splitDrafts, setSplitDrafts] = useState<Record<string, SplitDraft>>({});
  useEffect(() => setSplitDrafts({}), [mode, eventId, focus]);
  const split = draftedRows(splitDrafts, {});

  const hoursNum = hours.trim() ? Number(hours) : null;
  const peakNum = peakCtl.trim() ? Number(peakCtl) : null;
  const badNumbers =
    (hoursNum != null && !(hoursNum > 0 && hoursNum <= 40)) || (peakNum != null && !(peakNum > 0 && peakNum <= 200));
  const invalid = badNumbers || split.invalid;
  const splitBody = Object.keys(split.rows).length ? { split_by_phase: split.rows } : {};

  const body: SeasonPlanIn = fitness
    ? {
        mode: "fitness",
        weekly_hours_cap: hoursNum,
        focus,
        week_start_day: weekStart === 0 ? "sunday" : "monday",
        start: convert ? "this" : start,
        convert,
        use_instructions: useInstructions,
        ...splitBody,
      }
    : {
        mode: "event",
        event_id: eventId ?? null,
        weekly_hours_cap: hoursNum,
        ramp,
        peak_ctl: peakNum,
        week_start_day: weekStart === 0 ? "sunday" : "monday",
        start,
        use_instructions: useInstructions,
        sub_event_ids: subIds,
        ...splitBody,
      };
  // What the Next / Create button needs: an event in event mode, nothing in fitness.
  const ready = fitness || !!eventId;
  const bodyKey = JSON.stringify(body);

  // Live preview, debounced so typing an hours figure is one request.
  useEffect(() => {
    if (!ready || invalid) return;
    let cancelled = false;
    setLoadingPreview(true);
    const t = window.setTimeout(() => {
      api
        .previewSeasonPlan(body)
        .then((p) => {
          if (cancelled) return;
          setPreview(p);
          setPreviewError(null);
        })
        .catch((e) => !cancelled && setPreviewError(e instanceof Error ? e.message : String(e)))
        .finally(() => !cancelled && setLoadingPreview(false));
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bodyKey]);

  async function create() {
    if (!ready || invalid || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.createSeasonPlan(body);
      onCreated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function next() {
    if (!lastStep) setStep(Math.min(step, steps.length - 1) + 1);
    else create();
  }

  // Esc cancels; Ctrl+Enter moves on, and creates on the last step.
  useModalKeys(
    () => { if (!busy) onClose(); },
    () => { if (!busy && ready && !invalid) next(); },
  );

  const capped = preview?.revisions.some((r) => r.reason_code === "HOURS_CAP");
  // A setting the form leaves blank may come from the coach instructions.
  const fromInstr = (k: "weekly_hours_cap" | "ramp" | "peak_ctl") => preview?.settings_source?.[k] === "instructions";
  // ... or from the activity class in the athlete profile.
  const fromProfile = (k: "ramp" | "peak_ctl" | "recovery_cadence_weeks") => preview?.settings_source?.[k] === "profile";
  const classNote = `From your activity class${activityClass != null ? ` (${activityClass})` : ""} in Settings`;
  const everyWeeks = preview?.settings.recovery_cadence_weeks ?? 3;
  const everyWord = ({ 2: "two", 3: "three", 4: "four" } as Record<number, string>)[everyWeeks] ?? String(everyWeeks);
  const shownRamp = ramp ?? preview?.settings.ramp ?? "standard";
  const instr = preview?.instructions_view;
  const warnings = [
    preview?.targets?.compressed &&
      `Short runway: peak lowered to ${Math.round(preview.targets.peak_weekly_tss)} TSS/week.`,
    capped && `Your ${hoursNum} h a week holds the peak at ${Math.round(preview?.targets?.peak_weekly_tss ?? 0)} TSS/week.`,
    preview?.baseline.limited_history && "Limited history, so the first weeks use a conservative start.",
  ].filter(Boolean) as string[];

  return (
    <div onClick={() => !busy && onClose()} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 55, padding: 24 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 760, maxWidth: "100%", maxHeight: "92vh", display: "flex", flexDirection: "column", background: C.modalPanel, border: `1px solid ${C.modalBorder}`, borderRadius: 8, overflow: "hidden", boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "13px 18px", borderTop: `3px solid ${C.form}`, borderBottom: `1px solid ${C.modalBorder}`, background: C.bar }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>New season plan</span>
          <span style={{ display: "flex", alignItems: "center", gap: 16 }}>
            {steps.map((s, i) => (
              <span key={s} style={{ fontSize: 11, fontWeight: s === stepName ? 700 : 500, color: s === stepName ? C.text : C.mutedBright }}>
                {i + 1}. {s}
              </span>
            ))}
            <span onClick={() => !busy && onClose()} style={{ cursor: "pointer", fontSize: 18, color: C.modalLabel, lineHeight: 1 }}>✕</span>
          </span>
        </div>

        <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto" }}>
          {stepName === "Goal" && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <Choice active={!fitness} title="Event" body="Work back from a race: base, build, peak, taper." onClick={() => setMode("event")} />
                <Choice active={fitness} title="Fitness" body="No race. Choose each week how hard to go." onClick={() => setMode("fitness")} />
              </div>
              {fitness ? (
                <span style={{ fontSize: 12.5, color: C.mutedBright2, lineHeight: 1.5 }}>
                  The plan sets one week at a time. On the last day of each week you pick the next one: Increase, Stay, Decrease or Recovery, with a recommendation from your form and the week you had. A recovery week comes after every {everyWord} loading weeks{fromProfile("recovery_cadence_weeks") ? ` (${classNote.charAt(0).toLowerCase()}${classNote.slice(1)})` : ""}.
                </span>
              ) : (
              <Section label="Event">
                {upcoming.length ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {upcoming.map((e) => {
                      const on = e.event_id === eventId;
                      return (
                        <button
                          key={e.event_id}
                          onClick={() => setEventId(e.event_id)}
                          aria-pressed={on}
                          style={{ display: "flex", alignItems: "center", gap: 12, textAlign: "left", cursor: "pointer", padding: "10px 12px", borderRadius: 6, background: on ? C.inner2 : C.modalInner, border: `1px solid ${on ? C.form : C.modalControlBorder}`, color: C.text }}
                        >
                          {e.icon && <span style={{ fontSize: 16 }}>{e.icon}</span>}
                          <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{e.name}</span>
                          <span style={{ fontSize: 12, color: C.mutedBright2 }}>{typeLabel(e.type)}</span>
                          <span style={{ fontSize: 12, color: C.mutedBright2, minWidth: 56 }}>{dayMon(e.race_date)}</span>
                          <span style={{ fontSize: 12, color: C.modalLabel, minWidth: 72, textAlign: "right" }}>{weeksAway(e.race_date)} weeks away</span>
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <span style={{ fontSize: 12.5, color: C.mutedBright2 }}>
                    No upcoming event you are racing. Add one with Manage events, or switch Participating on.
                  </span>
                )}
              </Section>
              )}
            </>
          )}

          {stepName === "Sub events" && mainEvent && (
            <Section label={`Sub events before ${mainEvent.name}`} help="A sub event gets a lighter week around it, sized by its distance: a short race eases its own week, a marathon or longer also the week before, with a recovery week after. Your main event stays the goal of every week.">
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {candidates.map((e) => {
                  const on = subIds.includes(e.event_id);
                  return (
                    <button
                      key={e.event_id}
                      onClick={() => setSubOff((off) => (on ? [...off, e.event_id] : off.filter((x) => x !== e.event_id)))}
                      aria-pressed={on}
                      style={{ display: "flex", alignItems: "center", gap: 12, textAlign: "left", cursor: "pointer", padding: "10px 12px", borderRadius: 6, background: on ? C.inner2 : C.modalInner, border: `1px solid ${on ? C.form : C.modalControlBorder}`, color: C.text, opacity: on ? 1 : 0.75 }}
                    >
                      <span style={{ width: 14, height: 14, flex: "none", borderRadius: 3, border: `1px solid ${on ? C.form : C.modalControlBorder}`, background: on ? C.form : "transparent" }} />
                      {e.icon && <span style={{ fontSize: 16 }}>{e.icon}</span>}
                      <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{e.name}</span>
                      <span style={{ fontSize: 12, color: C.mutedBright2 }}>{typeLabel(e.type)}</span>
                      <span style={{ fontSize: 12, color: C.mutedBright2, minWidth: 56 }}>{dayMon(e.race_date)}</span>
                      <span style={{ fontSize: 12, color: C.modalLabel, minWidth: 72, textAlign: "right" }}>{on ? "Sub event" : "Not in plan"}</span>
                    </button>
                  );
                })}
              </div>
            </Section>
          )}

          {stepName === "Settings" && (
            <>
              {instr && <InstructionsBox view={instr} on={useInstructions} onToggle={() => setUseInstructions((v) => !v)} />}
              <Section label="Weekly hours available" help={preview ? `Average over the last 4 weeks: ${preview.baseline.avg_weekly_hours_4w.toFixed(1)} h. Leave blank for ${fromInstr("weekly_hours_cap") ? "the value from your coach instructions" : "no limit"}.` : "Leave blank for no limit."}>
                <input type="number" min={1} max={40} step={0.5} value={hours} placeholder={fromInstr("weekly_hours_cap") && preview?.settings.weekly_hours_cap ? `${preview.settings.weekly_hours_cap} (instructions)` : "no limit"} onChange={(e) => setHours(e.target.value)} style={inputStyle} />
              </Section>
              {convert && (
                <span style={{ fontSize: 12.5, color: C.mutedBright2, lineHeight: 1.5 }}>
                  Your event plan becomes a fitness plan: the weeks already done stay, and from this week on you choose each week's load.
                </span>
              )}
              {fitness && (
                <Section label="Focus">
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
                    {FOCUSES.map((f) => (
                      <button key={f.v} onClick={() => setFocus(f.v)} aria-pressed={focus === f.v} style={{ textAlign: "left", cursor: "pointer", padding: "10px 12px", borderRadius: 6, background: focus === f.v ? C.inner2 : C.modalInner, border: `1px solid ${focus === f.v ? C.form : C.modalControlBorder}`, display: "flex", flexDirection: "column", gap: 4 }}>
                        <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{f.label}</span>
                        <span style={{ fontSize: 11.5, color: C.mutedBright2, lineHeight: 1.4 }}>{f.help}</span>
                      </button>
                    ))}
                  </div>
                </Section>
              )}
              {!fitness && (
              <Section label="Build-up pace" help={ramp == null && fromInstr("ramp") ? "Set by your coach instructions; pick another to override them." : ramp == null && fromProfile("ramp") ? `${classNote}; pick another to override it.` : undefined}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                  {RAMPS.map((r) => (
                    <button key={r.v} onClick={() => setRamp(r.v)} aria-pressed={shownRamp === r.v} style={{ textAlign: "left", cursor: "pointer", padding: "10px 12px", borderRadius: 6, background: shownRamp === r.v ? C.inner2 : C.modalInner, border: `1px solid ${shownRamp === r.v ? C.form : C.modalControlBorder}`, display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{r.label}</span>
                      <span style={{ fontSize: 11.5, color: C.mutedBright2, lineHeight: 1.4 }}>{r.help}</span>
                    </button>
                  ))}
                </div>
              </Section>
              )}
              {!fitness && (
              <Section label="Peak fitness (CTL)" help={`The fitness the build aims for; a peak week is about 7 x this in TSS.${preview?.targets && !peakCtl ? (fromProfile("peak_ctl") ? ` ${classNote}: ${Math.round(preview.targets.peak_ctl)}.` : fromInstr("peak_ctl") ? ` From your coach instructions: ${Math.round(preview.targets.peak_ctl)}.` : ` Default for this distance: ${Math.round(preview.targets.peak_ctl)}.`) : ""}`}>
                <input type="number" min={10} max={200} step={1} value={peakCtl} placeholder={preview?.targets ? String(Math.round(preview.targets.peak_ctl)) : ""} onChange={(e) => setPeakCtl(e.target.value)} style={inputStyle} />
              </Section>
              )}
              {!convert && (
              <Section label="Start">
                <div style={{ display: "flex", gap: 8 }}>
                  {([["this", "This week"], ["next", "Next week"]] as const).map(([v, label]) => {
                    const on = (start ?? (preview && preview.weeks[0]?.status === "next" ? "next" : "this")) === v;
                    return (
                      <button key={v} onClick={() => setStart(v)} aria-pressed={on} style={{ cursor: "pointer", padding: "8px 14px", borderRadius: 6, fontSize: 12.5, fontWeight: 600, background: on ? C.inner2 : C.modalInner, border: `1px solid ${on ? C.form : C.modalControlBorder}`, color: C.text }}>
                        {label}
                      </button>
                    );
                  })}
                </div>
              </Section>
              )}
              {preview && preview.weeks.some((w) => w.split) && (
                <Section label="Swim / bike / run split" help="Each week's TSS target, shared out by sport. Every phase has its own mix; you can still change any single week later.">
                  <SplitTable
                    rows={{ ...tableOf(preview.weeks, fitness), ...split.rows }}
                    drafts={splitDrafts}
                    onChange={setSplitDrafts}
                    inputStyle={inputStyle}
                    labelColor={C.modalLabel}
                  />
                </Section>
              )}
              {badNumbers && <span style={{ fontSize: 12, color: C.red }}>Hours must be between 0 and 40, and peak CTL between 0 and 200.</span>}
              {split.invalid && <span style={{ fontSize: 12, color: C.red }}>Each split must be whole percents adding up to 100.</span>}
            </>
          )}

          {stepName === "Preview" && (
            <>
              {preview ? (
                <>
                  <div style={{ height: 220, background: C.card, border: `1px solid ${C.cardBorder}`, borderRadius: 3, padding: "8px 8px 0", opacity: loadingPreview ? 0.6 : 1 }}>
                    <PlanBars weeks={preview.weeks} mode={preview.mode} h={200} />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <span style={{ fontSize: 12.5, color: C.modalLabel, lineHeight: 1.45 }}>{preview.summary.line1}</span>
                    <span style={{ fontSize: 12.5, color: C.text, lineHeight: 1.45 }}>{preview.summary.line2}</span>
                    {fitness && (
                      <span style={{ fontSize: 12, color: C.mutedBright2, lineHeight: 1.45 }}>
                        You will choose each week's load on the last day of the week. The dotted weeks are a projection, not a commitment.
                      </span>
                    )}
                  </div>
                  {warnings.map((w) => (
                    <span key={w} style={{ fontSize: 12, color: C.fat }}>{w}</span>
                  ))}
                </>
              ) : (
                <span style={{ fontSize: 12.5, color: C.mutedBright2 }}>{loadingPreview ? "Building the preview..." : "No preview yet."}</span>
              )}
            </>
          )}

          {previewError && <span style={{ fontSize: 12, color: C.red }}>{previewError}</span>}
          {error && <span style={{ fontSize: 12, color: C.red }}>{error}</span>}
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "12px 18px", borderTop: `1px solid ${C.modalBorder}`, background: C.bar }}>
          <button onClick={() => setStep(Math.min(step, steps.length - 1) - 1)} disabled={stepName === "Goal" || busy} style={{ ...btnStyle, background: "transparent", color: C.modalLabel, border: `1px solid ${C.modalControlBorder}`, visibility: stepName === "Goal" ? "hidden" : "visible" }}>
            Back
          </button>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={onClose} disabled={busy} style={{ ...btnStyle, background: "transparent", color: C.modalLabel, border: `1px solid ${C.modalControlBorder}` }}>
              Cancel
            </button>
            <button onClick={next} disabled={busy || !ready || invalid || (lastStep && !preview)} style={{ ...btnStyle, background: C.form, color: "#fff", opacity: busy || !ready || invalid ? 0.5 : 1 }}>
              {!lastStep ? "Next" : busy ? "Creating..." : convert ? "Convert plan" : "Create plan"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Choice({ title, body, active, note, onClick }: { title: string; body: string; active?: boolean; note?: string; onClick?: () => void }) {
  return (
    <button onClick={onClick} aria-pressed={active} style={{ textAlign: "left", cursor: "pointer", padding: "14px 16px", borderRadius: 6, background: active ? C.inner2 : C.modalInner, border: `1px solid ${active ? C.form : C.modalControlBorder}`, display: "flex", flexDirection: "column", gap: 5, opacity: active ? 1 : 0.75 }}>
      <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{title}</span>
      <span style={{ fontSize: 12, color: C.mutedBright2, lineHeight: 1.4 }}>{body}</span>
      {note && <span style={{ fontSize: 10, letterSpacing: ".06em", textTransform: "uppercase", color: C.mutedBright }}>{note}</span>}
    </button>
  );
}

// What the plan took from the athlete's coach instructions, with the switch
// that leaves them out. Days and session details are listed apart: the 7-day
// plan follows those itself.
function InstructionsBox({ view, on, onToggle }: { view: SeasonInstructionsView; on: boolean; onToggle: () => void }) {
  return (
    <div style={{ background: C.modalInner, border: `1px solid ${C.modalControlBorder}`, borderRadius: 6, padding: "12px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <span style={{ fontSize: 10.5, letterSpacing: ".06em", textTransform: "uppercase", color: C.mutedBright }}>From your coach instructions</span>
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
          <button
            type="button"
            role="switch"
            aria-checked={on}
            onClick={onToggle}
            className="participation-switch"
            style={{ position: "relative", flex: "none", width: 38, height: 22, padding: 0, border: "none", borderRadius: 11, background: on ? C.form : "#3a3d44", cursor: "inherit" }}
          >
            <span style={{ position: "absolute", top: 3, left: on ? 19 : 3, width: 16, height: 16, borderRadius: 8, background: "#fff", transition: "left 150ms ease" }} />
          </button>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: on ? C.text : C.muted }}>Use my instructions</span>
        </label>
      </div>
      {!on ? (
        <span style={{ fontSize: 12.5, color: C.mutedBright2 }}>Left out of this plan. The 7-day plan still follows them.</span>
      ) : view.error ? (
        <span style={{ fontSize: 12.5, color: C.red }}>Could not read your coach instructions ({view.error}). The plan is built without them.</span>
      ) : (
        <>
          {view.summary && <span style={{ fontSize: 12.5, color: C.text, lineHeight: 1.45 }}>{view.summary}</span>}
          {view.applied.map((line) => (
            <span key={line} style={{ fontSize: 12.5, color: C.successBright }}>{line}</span>
          ))}
          {view.left_to_weekly_plan.length > 0 && (
            <span style={{ fontSize: 12, color: C.mutedBright2, lineHeight: 1.45 }}>
              Left to the weekly plan: {view.left_to_weekly_plan.join("; ")}.
            </span>
          )}
        </>
      )}
    </div>
  );
}

function Section({ label, help, children }: { label: string; help?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 10.5, letterSpacing: ".06em", textTransform: "uppercase", color: C.mutedBright }}>{label}</span>
      {children}
      {help && <span style={{ fontSize: 11.5, color: C.mutedBright2, lineHeight: 1.4 }}>{help}</span>}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: 140,
  background: C.bar,
  border: `1px solid ${C.modalControlBorder}`,
  borderRadius: 6,
  padding: "9px 11px",
  fontSize: 12.5,
  color: C.text,
  outline: "none",
};

const btnStyle: React.CSSProperties = {
  cursor: "pointer",
  fontSize: 12.5,
  fontWeight: 600,
  padding: "9px 16px",
  borderRadius: 6,
  border: "none",
};
