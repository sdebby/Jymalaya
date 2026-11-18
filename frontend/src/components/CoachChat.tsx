import { useEffect, useRef, useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { C, DISCIPLINE_COLOR } from "../theme";
import { api, isAbortError } from "../api";
import {
  clearChat,
  loadChat,
  loadIncludeContext,
  saveChat,
  saveIncludeContext,
  type ChatMsg,
} from "../chatStore";
import { GENERAL_SUGGESTIONS, syncSuggestions } from "../derive";
import { hm, km, monthDay, pace, round } from "../format";
import type { CoachAttachment, Plan, PlanAttachment, SeasonWeekAttachment, WorkoutAttachment, WorkoutRow } from "../types";

// Only the most recent turns go to Claude; the panel still shows the full 7 days.
const HISTORY_SENT_TO_API = 20;

// Slash commands surfaced in the hint popup when the user types "/".
const COMMANDS = [
  { cmd: "/last", desc: "send your latest workout to the coach — add a question after it" },
  { cmd: "/summary", desc: "show the current plan's coach summary (from the last sync)" },
  { cmd: "/clear", desc: "reset the chat and delete the saved history" },
  { cmd: "/help", desc: "list all chat commands" },
];

// The /help reply, built from the same list that drives the hint popup.
const HELP_TEXT =
  "**Chat commands**\n" + COMMANDS.map((c) => `\`${c.cmd}\` — ${c.desc}`).join("\n");

const FAILED_TEXT = "I couldn't reach the coach just now — please try again in a moment.";

// Shown when a command that sends training data is used with the context
// toggle off, rather than quietly sending the data anyway.
const NO_CONTEXT_TEXT =
  "Include context is off, so I'm not looking at your training data. Switch **Include context** on next to Send and ask again.";

// Send / Stop share the composer button shape; only the colours differ.
const BTN: CSSProperties = {
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  padding: "0 18px",
  borderRadius: 6,
  fontSize: 12.5,
  fontWeight: 600,
  flex: "none",
};

// Stop only abandons the browser's wait: the backend turn keeps running, so a
// plan edit the coach had already made stands. We deliberately don't refetch
// here -- a dashboard refresh remounts every panel and would yank the athlete
// away from the chat they are still using.
const STOPPED_TEXT =
  "Stopped. The coach may still finish on the server — reload the page to see any plan change it made.";

function greeting(): ChatMsg {
  return {
    role: "coach",
    text: "Hi — I'm your coach. Ask about tomorrow's session, how to handle fatigue, or what to do this weekend.\nType /help to see the chat commands.",
    ts: Date.now(),
  };
}

// Full readable dump of the most recent workout, sent to the coach on /last.
function formatLastWorkout(workouts: WorkoutRow[]): string | null {
  if (!workouts.length) return null;
  const w = [...workouts].sort((a, b) => b.time.localeCompare(a.time))[0];
  const lines = [
    `date: ${new Date(w.time).toLocaleString()}`,
    `discipline: ${w.discipline}`,
    w.title && `title: ${w.title}`,
    `duration: ${hm(w.duration_sec)} h`,
    w.distance_m != null && `distance: ${km(w.distance_m)} km`,
    w.tss != null && `TSS: ${round(w.tss)}`,
    w.intensity_factor != null && `intensity factor: ${w.intensity_factor.toFixed(2)}`,
    w.avg_hr != null && `avg HR: ${round(w.avg_hr)} bpm`,
    w.avg_power != null && `avg power: ${round(w.avg_power)} W`,
    w.normalized_power != null && `normalized power: ${round(w.normalized_power)} W`,
    w.avg_pace_sec_per_km != null && `avg pace: ${pace(w.avg_pace_sec_per_km)} /km`,
    w.avg_pace_sec_per_100m != null && `avg pace: ${pace(w.avg_pace_sec_per_100m)} /100m`,
    w.is_brick_split && `part of a brick session`,
  ].filter(Boolean) as string[];
  if (w.zones_time_sec) {
    try {
      const zones = JSON.parse(w.zones_time_sec) as Record<string, number>;
      const z = Object.keys(zones)
        .sort()
        .map((k) => `${k} ${Math.round(zones[k] / 60)}m`)
        .join(" · ");
      if (z) lines.push(`time in HR zones: ${z}`);
    } catch {
      /* ignore bad zone JSON */
    }
  }
  return lines.join("\n");
}

// Markdown rendering of the cached plan for /summary — same numbers as the
// post-sync PlanSummaryModal, shown locally in the chat without a Claude call.
function formatPlanSummary(plan: Plan): string {
  const trained = (plan.days ?? []).filter((d) => !d.is_rest_day);
  const sessions = trained.reduce((a, d) => a + d.sessions.length, 0);
  const totalTss = trained.reduce(
    (a, d) => a + d.sessions.reduce((s, x) => s + (x.estimated_tss || 0), 0),
    0,
  );
  const totalHours =
    trained.reduce((a, d) => a + d.sessions.reduce((s, x) => s + (x.duration_min || 0), 0), 0) / 60;

  const generated = plan._meta?.generated_at
    ? ` · generated ${new Date(plan._meta.generated_at).toLocaleString()}`
    : "";
  const chips = [
    plan.periodization_phase && `**Phase:** ${plan.periodization_phase}`,
    plan.weekly_tss_target != null && `**Target:** ${Math.round(plan.weekly_tss_target)} TSS`,
    `**Planned:** ${totalHours.toFixed(1)} h · ${Math.round(totalTss)} TSS`,
    `**Sessions:** ${sessions}`,
  ]
    .filter(Boolean)
    .join(" · ");

  const parts = [`**Current 7-day plan**${generated}`, chips];
  if (plan.summary) parts.push(plan.summary);
  if (plan.coach_notes) parts.push(`**Coach notes**\n${plan.coach_notes}`);
  return parts.join("\n\n");
}

// Readable dump of an attached planned session, prepended to the user's question.
// The "plan reference" line gives the coach the exact (date, index) address its
// plan-editing tools need.
function formatPlanSession(a: PlanAttachment): string {
  const s = a.session;
  const lines = [
    `day: ${a.dayName} ${a.date}`,
    `discipline: ${s.discipline}`,
    s.title && `title: ${s.title}`,
    s.type && `type: ${s.type}`,
    s.duration_min != null && `duration: ${s.duration_min} min`,
    s.estimated_tss != null && `estimated TSS: ${s.estimated_tss}`,
    s.zones_target &&
      `target zones: ${Object.entries(s.zones_target)
        .map(([z, v]) => `${z} ${v}`)
        .join(" · ")}`,
    s.notes && `notes: ${s.notes}`,
  ].filter(Boolean) as string[];
  if (s.structure?.length) {
    lines.push("structure:");
    for (const seg of s.structure) {
      const parts = [
        seg.segment,
        seg.duration_min != null ? `${seg.duration_min} min` : null,
        seg.zone,
        seg.target_power_w ? `${seg.target_power_w} W` : null,
        seg.notes,
      ].filter(Boolean);
      lines.push(`  - ${parts.join(", ")}`);
    }
  }
  lines.push(`plan reference: date=${a.date}, session_index=${a.sessionIndex}`);
  return lines.join("\n");
}

// A completed workout attached from its detail modal, with whatever analysis has
// already been produced. No plan reference line: this session is history, so the
// coach's plan-editing tools do not address it.
function formatCompletedWorkout(a: WorkoutAttachment): string {
  const w = a.workout;
  const lines = [
    `day: ${a.dayName} ${a.date}`,
    `discipline: ${w.discipline}`,
    w.title && `title: ${w.title}`,
    w.duration_sec != null && `duration: ${Math.round(w.duration_sec / 60)} min`,
    w.distance_m != null &&
      `distance: ${w.discipline === "swim" ? `${Math.round(w.distance_m)} m` : `${(w.distance_m / 1000).toFixed(2)} km`}`,
    w.tss != null && `TSS: ${Math.round(w.tss)}`,
    w.avg_hr != null && `avg HR: ${Math.round(w.avg_hr)} bpm`,
    w.avg_power != null && `avg power: ${Math.round(w.avg_power)} W`,
    w.intensity_factor != null && `IF: ${w.intensity_factor.toFixed(2)}`,
  ].filter(Boolean) as string[];

  const an = a.analysis;
  if (an) {
    lines.push("", "your analysis of this session:");
    if (an.plan_adherence?.verdict) lines.push(`  execution: ${an.plan_adherence.verdict}`);
    for (const item of an.what_to_improve ?? []) lines.push(`  to improve: ${item}`);
  }
  return lines.join("\n");
}

// A season-plan week attached from its week panel: the week's targets and the
// address the adjust_season_plan tool uses (the week's first day).
function formatSeasonWeek(a: SeasonWeekAttachment): string {
  const w = a.week;
  const lines = [
    `season plan week ${w.index} of ${a.weeksInPlan}, starting ${a.dayName} ${a.date}`,
    `phase: ${w.phase}, kind: ${w.kind}`,
    `target: ${Math.round(w.target_tss)} TSS (${w.target_hours.toFixed(1)} h)`,
    w.actual_tss != null && `done so far: ${Math.round(w.actual_tss)} TSS`,
    w.focus && `focus: ${w.focus}`,
    w.pinned && `pinned by the athlete${w.pin_note ? `: ${w.pin_note}` : ""}`,
    `season plan reference: week_start=${a.date}`,
  ].filter(Boolean) as string[];
  return lines.join("\n");
}

// The chip's subject: what to colour it by and what to call it. A season week
// has no discipline, so it takes the muted dot.
function attachmentSubject(a: CoachAttachment): { discipline: string; label: string } {
  if (a.kind === "season_week") return { discipline: "other", label: `Season week ${a.week.index}, ${a.week.phase}` };
  return a.kind === "workout"
    ? { discipline: a.workout.discipline, label: a.workout.title || a.workout.discipline }
    : { discipline: a.session.discipline, label: a.session.title || a.session.discipline };
}

function attachmentWhen(a: CoachAttachment): string {
  if (a.kind === "season_week") return "season plan";
  return a.kind === "workout" ? "completed" : "planned";
}

function attachmentText(a: CoachAttachment): string {
  if (a.kind === "season_week") return `About this season plan week:\n${formatSeasonWeek(a)}`;
  return a.kind === "workout"
    ? `About this completed workout:\n${formatCompletedWorkout(a)}`
    : `About this planned workout:\n${formatPlanSession(a)}`;
}

// Speech bubble with a spark, from assets/coach-icon.svg. Inlined rather than
// imported so it picks up the theme's form/fit colors.
function CoachIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
      <path
        d="M4.2 2.6 H11.8 A2.6 2.6 0 0 1 14.4 5.2 V8.2 A2.6 2.6 0 0 1 11.8 10.8 H7.6 L4.6 13.4 V10.8 A2.6 2.6 0 0 1 2 8.2 V5.2 A2.6 2.6 0 0 1 4.2 2.6 Z"
        stroke={C.form}
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path d="M8 4.5 L8.75 6 L10.2 6.7 L8.75 7.4 L8 8.9 L7.25 7.4 L5.8 6.7 L7.25 6 Z" fill={C.fit} />
    </svg>
  );
}

export function CoachChat({
  workouts,
  plan,
  tsb,
  attachment,
  onClearAttachment,
  onPlanUpdated,
}: {
  workouts: WorkoutRow[];
  plan: Plan | null;
  tsb: number | null | undefined;
  attachment: CoachAttachment | null;
  onClearAttachment: () => void;
  onPlanUpdated: () => void;
}) {
  const [messages, setMessages] = useState<ChatMsg[]>(() => {
    const saved = loadChat();
    return saved.length ? saved : [greeting()];
  });
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  // "Include context": on = the backend sends the athlete's data (and the coach
  // can edit the plan); off = a general triathlon question, no data sent.
  const [includeContext, setIncludeContext] = useState(loadIncludeContext);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // The request the Stop button aborts. `kind` decides what stopping means:
  // only a coach turn can have changed the plan behind our back.
  const pending = useRef<{ ctrl: AbortController; kind: "coach" | "summary" } | null>(null);

  // Command hint: shown while the input is a "/" prefix that hasn't been completed yet.
  const cmdHints = input.startsWith("/")
    ? COMMANDS.filter((c) => c.cmd.startsWith(input.trim()) && input.trim() !== c.cmd)
    : [];

  // The three chips: the follow-up questions the coach ended its latest reply
  // with. A clean chat (nothing asked yet), or one where no reply carried any,
  // gets questions about the last sync instead -- or general ones with the
  // context toggle off, since those must not name the athlete's data.
  const asked = messages.some((m) => m.role === "user");
  const fromCoach = asked
    ? [...messages].reverse().find((m) => m.role === "coach" && m.suggestions?.length)?.suggestions
    : undefined;
  const suggestions = fromCoach ?? (includeContext ? syncSuggestions(workouts, plan, tsb) : GENERAL_SUGGESTIONS);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  useEffect(() => {
    saveChat(messages);
  }, [messages]);

  // A workout was just attached from the Predicted Week panel: bring the chat
  // into view and put the cursor in the input so the user can type their question.
  // Attaching a workout IS a request to discuss it, so it turns the context
  // toggle back on rather than silently dropping the attachment on send.
  useEffect(() => {
    if (attachment) {
      scrollRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
      inputRef.current?.focus();
      setIncludeContext((on) => {
        if (!on) saveIncludeContext(true);
        return true;
      });
    }
  }, [attachment]);

  // Off means no training data leaves the browser, and an attached workout is
  // exactly that -- so detach it instead of keeping a chip that won't be sent.
  function toggleContext() {
    const next = !includeContext;
    setIncludeContext(next);
    saveIncludeContext(next);
    if (!next && attachment) onClearAttachment();
  }

  async function ask(text: string) {
    let t = text.trim();
    if (!t || loading) return;

    // Slash commands are handled locally before anything goes to the API.
    if (t.startsWith("/")) {
      const [cmd, ...rest] = t.split(/\s+/);
      const extra = rest.join(" ");
      if (cmd === "/clear") {
        setInput("");
        resetChat();
        return;
      }
      if (cmd === "/help") {
        setInput("");
        setMessages((m) => [...m, { role: "coach", text: HELP_TEXT, ts: Date.now() }]);
        return;
      }
      if (cmd === "/summary") {
        // Answered locally from the cached plan — no sync, no Claude call.
        setInput("");
        setLoading(true);
        const ctrl = beginRequest("summary");
        let plan: Plan | null;
        try {
          plan = await api.recommendation(ctrl.signal);
        } catch {
          return; // stopped by the user: stop() has already restored the UI
        } finally {
          endRequest(ctrl);
        }
        setMessages((m) => [
          ...m,
          {
            role: "coach",
            text: plan
              ? formatPlanSummary(plan)
              : "No plan yet — run a sync (or trigger a recommendation) first.",
            ts: Date.now(),
          },
        ]);
        setLoading(false);
        return;
      }
      if (cmd === "/last") {
        // The whole command is a workout dump; it has no meaning with the
        // context toggle off, and must not sneak the data past it.
        if (!includeContext) {
          setInput("");
          setMessages((m) => [...m, { role: "coach", text: NO_CONTEXT_TEXT, ts: Date.now() }]);
          return;
        }
        const summary = formatLastWorkout(workouts);
        if (!summary) {
          setInput("");
          setMessages((m) => [
            ...m,
            { role: "coach", text: "No workouts synced yet — run a Garmin sync first.", ts: Date.now() },
          ]);
          return;
        }
        t =
          `Here is my last workout:\n${summary}\n\n` +
          (extra || "Analyze this workout for me — how did it go, and what should I take from it?");
      } else {
        setInput("");
        setMessages((m) => [
          ...m,
          { role: "coach", text: `Unknown command ${cmd} — type /help to see the available commands.`, ts: Date.now() },
        ]);
        return;
      }
    }

    // An attached workout rides along with the question (not with slash
    // commands — those carry their own context). Attaching turns the toggle
    // back on, so the guard here is belt-and-braces.
    if (attachment && includeContext && !text.trim().startsWith("/")) {
      t = `${attachmentText(attachment)}\n\n${t}`;
      onClearAttachment();
    }

    const history = messages.slice(-HISTORY_SENT_TO_API).map((m) => ({ role: m.role, text: m.text }));
    setMessages((m) => [...m, { role: "user", text: t, ts: Date.now() }]);
    setInput("");
    setLoading(true);
    const ctrl = beginRequest("coach");
    let res: Awaited<ReturnType<typeof api.coach>>;
    try {
      res = await api.coach(t, history, includeContext, ctrl.signal);
    } catch (e) {
      if (isAbortError(e)) return; // stopped by the user; stop() handled the UI
      res = null;
    } finally {
      endRequest(ctrl);
    }
    setMessages((m) => [
      ...m,
      {
        role: "coach",
        text: res?.reply ?? FAILED_TEXT,
        ts: Date.now(),
        planUpdated: res?.plan_updated || undefined,
        settingsUpdated: res?.settings_updated || undefined,
        suggestions: res?.suggestions?.length ? res.suggestions : undefined,
        error: !res || res.error || undefined,
      },
    ]);
    setLoading(false);
    // The coach edited the stored plan, or saved new thresholds (which rewrote
    // the last 7 days' TSS and the load series): refetch the dashboard.
    if (res?.plan_updated || res?.settings_updated) onPlanUpdated();
  }

  function beginRequest(kind: "coach" | "summary"): AbortController {
    const ctrl = new AbortController();
    pending.current = { ctrl, kind };
    return ctrl;
  }

  // Clear the pending slot unless Stop already replaced/cleared it.
  function endRequest(ctrl: AbortController) {
    if (pending.current?.ctrl === ctrl) pending.current = null;
  }

  // Stop waiting for the in-flight answer and hand the input back to the user.
  function stop() {
    const p = pending.current;
    if (!p) return;
    pending.current = null;
    p.ctrl.abort();
    setLoading(false);
    setMessages((m) => [
      ...m,
      { role: "coach", text: p.kind === "coach" ? STOPPED_TEXT : "Stopped.", ts: Date.now() },
    ]);
    inputRef.current?.focus();
  }

  function resetChat() {
    clearChat();
    setMessages([greeting()]);
  }

  return (
    <div style={{ background: C.card, border: `1px solid ${C.cardBorder}`, borderRadius: 3, display: "flex", flexDirection: "column", height: 440 }}>
      <div style={{ padding: "11px 14px", display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: `1px solid ${C.cardBorder}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{ width: 22, height: 22, display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
            <CoachIcon />
          </div>
          <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.25 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: C.text }}>Coach</span>
            <span style={{ fontSize: 10, color: C.muted }}>Ask about your training · last 7 days saved</span>
          </div>
        </div>
        <div
          onClick={resetChat}
          title="Clear the saved conversation"
          style={{ cursor: "pointer", fontSize: 10.5, color: C.muted, border: `1px solid ${C.cardBorder}`, borderRadius: 4, padding: "3px 9px" }}
        >
          Clear
        </div>
      </div>

      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 3, alignItems: m.role === "user" ? "flex-end" : "flex-start" }}>
            <div
              style={{
                maxWidth: "76%",
                background: m.role === "user" ? C.form : C.inner2,
                border: `1px solid ${m.role === "user" ? C.form : C.cardBorder}`,
                color: m.role === "user" ? "#fff" : m.error ? C.red : "#dfe0e6",
                fontWeight: m.error ? 700 : undefined,
                borderRadius: 10,
                padding: "9px 13px",
                fontSize: 12.5,
                lineHeight: 1.55,
                // Markdown supplies its own block layout for coach replies.
                whiteSpace: m.role === "user" || m.error ? "pre-wrap" : "normal",
              }}
            >
              {/* An error is backend text, not a coach reply: shown verbatim, not as markdown. */}
              {m.role === "coach" && !m.error ? (
                <div className="coach-md">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{m.text}</ReactMarkdown>
                </div>
              ) : (
                m.text
              )}
            </div>
            {m.planUpdated && (
              <span style={{ fontSize: 9.5, color: C.fit, paddingLeft: 4 }}>plan updated</span>
            )}
            {m.settingsUpdated && (
              <span style={{ fontSize: 9.5, color: C.fit, paddingLeft: 4 }}>settings updated</span>
            )}
          </div>
        ))}
        {loading && (
          <div style={{ display: "flex", justifyContent: "flex-start" }}>
            <div
              role="status"
              aria-label="Coach is thinking"
              style={{ background: C.inner2, border: `1px solid ${C.cardBorder}`, color: C.muted2, borderRadius: 10, padding: "9px 13px", fontSize: 12.5 }}
            >
              <span className="typing-dots">
                <span />
                <span />
                <span />
              </span>
            </div>
          </div>
        )}
      </div>

      <div style={{ padding: "0 16px 10px", display: "flex", gap: 8, flexWrap: "wrap" }}>
        {!loading && suggestions.map((s) => (
          <div
            key={s}
            onClick={() => ask(s)}
            style={{ cursor: "pointer", fontSize: 11, color: C.textChip, background: C.inner2, border: `1px solid ${C.cardBorder}`, borderRadius: 14, padding: "5px 11px" }}
          >
            {s}
          </div>
        ))}
      </div>

      {cmdHints.length > 0 && (
        <div style={{ margin: "0 16px 8px", background: C.inner2, border: `1px solid ${C.cardBorder}`, borderRadius: 6, overflow: "hidden" }}>
          <div style={{ padding: "5px 11px", fontSize: 9.5, letterSpacing: ".05em", textTransform: "uppercase", color: C.muted, borderBottom: `1px solid ${C.cardBorder}` }}>
            Commands · Tab to complete
          </div>
          {cmdHints.map((c) => (
            <div
              key={c.cmd}
              onClick={() => {
                setInput(c.cmd + " ");
                inputRef.current?.focus();
              }}
              style={{ cursor: "pointer", display: "flex", alignItems: "baseline", gap: 10, padding: "6px 11px" }}
            >
              <span style={{ fontSize: 12, fontWeight: 600, color: C.text, fontFamily: "monospace" }}>{c.cmd}</span>
              <span style={{ fontSize: 11, color: C.muted2 }}>{c.desc}</span>
            </div>
          ))}
        </div>
      )}

      {attachment && (
        <div style={{ margin: "0 16px 8px", display: "flex", alignItems: "center", gap: 8, background: C.inner2, border: `1px solid ${C.cardBorder}`, borderRadius: 6, padding: "6px 11px" }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: DISCIPLINE_COLOR[attachmentSubject(attachment).discipline] ?? C.muted, flex: "none" }} />
          <span style={{ fontSize: 11.5, color: C.text, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {attachmentSubject(attachment).label}
          </span>
          <span style={{ fontSize: 10.5, color: C.muted2, flex: "none" }}>
            {attachment.dayName} · {monthDay(attachment.date)} · {attachmentWhen(attachment)}
          </span>
          <span
            onClick={onClearAttachment}
            title="Detach"
            style={{ cursor: "pointer", marginLeft: "auto", fontSize: 12, color: C.muted, lineHeight: 1 }}
          >
            ✕
          </span>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, padding: "12px 16px", borderTop: `1px solid ${C.cardBorder}` }}>
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              ask(input);
            } else if (e.key === "Tab" && cmdHints.length > 0) {
              e.preventDefault();
              setInput(cmdHints[0].cmd + " ");
            }
          }}
          placeholder={
            attachment
              ? "ask about the attached workout — why? too hard? make it shorter?"
              : includeContext
                ? "e.g. what should I do tomorrow? — or /help"
                : "e.g. what should I eat before a half? — general questions only"
          }
          style={{ flex: 1, background: C.bar, border: `1px solid ${C.cardBorder}`, borderRadius: 6, padding: "9px 12px", fontSize: 12.5, color: C.text, outline: "none", fontFamily: "inherit" }}
        />
        <div
          onClick={toggleContext}
          title={
            includeContext
              ? "Your training data, plan and form are sent with the question — switch off for general questions"
              : "No training data is sent — the coach answers general questions only, and cannot change your plan"
          }
          style={{ display: "flex", alignItems: "center", gap: 7, cursor: "pointer", flex: "none", padding: "0 2px", userSelect: "none" }}
        >
          {/* Pill switch: the track carries the state colour, the knob slides. */}
          <span
            style={{
              width: 26,
              height: 15,
              borderRadius: 999,
              flex: "none",
              display: "flex",
              alignItems: "center",
              padding: 2,
              background: includeContext ? C.form : C.inner2,
              border: `1px solid ${includeContext ? C.form : C.cardBorder}`,
              transition: "background .15s, border-color .15s",
            }}
          >
            <span
              style={{
                width: 11,
                height: 11,
                borderRadius: "50%",
                background: includeContext ? "#fff" : C.muted2,
                transform: `translateX(${includeContext ? 11 : 0}px)`,
                transition: "transform .15s, background .15s",
              }}
            />
          </span>
          <span style={{ fontSize: 11, color: includeContext ? C.textChip : C.muted }}>
            Include context
          </span>
        </div>
        {loading ? (
          <div
            onClick={stop}
            title="Stop waiting for the answer"
            style={{ ...BTN, background: "transparent", border: `1px solid ${C.cardBorder}`, color: C.textChip }}
          >
            Stop
          </div>
        ) : (
          <div onClick={() => ask(input)} style={{ ...BTN, background: C.form, color: "#fff" }}>
            Send
          </div>
        )}
      </div>
    </div>
  );
}
