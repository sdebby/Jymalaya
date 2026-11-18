import { useState } from "react";
import { C } from "../theme";
import { api } from "../api";
import { useModalKeys } from "../useModalKeys";
import { isParticipating, isPastEvent, localISO, NOT_RACING_OPACITY, PAST_EVENT_OPACITY } from "../derive";
import { EVENT_TYPES } from "../types";
import type { RaceEvent, RaceEventIn, SeasonPlan } from "../types";

// True when a just-saved event could become a sub event of the active event
// plan: racing, dated from today to before the plan's race, not already in the
// plan, and new or moved to a new date (an unchanged event is not asked again).
function subEventCandidate(plan: SeasonPlan | null | undefined, saved: RaceEvent, prevDate: string | undefined): boolean {
  if (!plan || plan.mode !== "event" || plan.status !== "active" || !plan.event) return false;
  if (saved.event_id === plan.event.event_id || (plan.sub_events ?? []).some((s) => s.event_id === saved.event_id)) return false;
  if (!isParticipating(saved) || !saved.race_date) return false;
  if (prevDate !== undefined && prevDate === saved.race_date) return false;
  return saved.race_date >= localISO(new Date()) && saved.race_date < plan.event.race_date;
}

const ICONS = ["🏆", "🥇", "🏅", "🎯", "🏊", "🚴", "🏃", "🔥", "⚡", "🌊", "⛰️", "🏁", "💪", "☀️"];

// "H:MM:SS" / "M:SS" -> seconds; blank/invalid -> null.
function hmsToSec(str: string): number | null {
  const t = str.trim();
  if (!t) return null;
  const parts = t.split(":").map((p) => Number(p));
  if (parts.some((n) => Number.isNaN(n))) return null;
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 1) return parts[0];
  return null;
}

function secToHMS(sec?: number | null): string {
  if (!sec) return "";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.round(sec % 60);
  return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

const EMPTY: RaceEventIn = {
  name: "",
  type: "olympic",
  race_date: "",
  location_name: "",
  location_lat: null,
  location_lon: null,
  target_finish_time_sec: null,
  sub_goals: [],
  icon: "🏆",
  website: "",
  notes: "",
  participating: true,
};

export function EventModal({
  events,
  initialEventId,
  onClose,
  onSaved,
  onPlan,
  seasonPlan,
}: {
  events: RaceEvent[];
  // Event to open on: an event_id to edit it, null to start a new event,
  // undefined to fall back to the first event.
  initialEventId?: string | null;
  onClose: () => void;
  onSaved: () => void;
  // "Save and plan": save, close, then open the season plan modal on this event.
  onPlan?: (eventId: string) => void;
  // The season plan, to offer a new earlier event as one of its sub events.
  seasonPlan?: SeasonPlan | null;
}) {
  const initial =
    initialEventId === null
      ? null
      : events.find((g) => g.event_id === initialEventId) ?? (initialEventId === undefined ? events[0] ?? null : null);

  // The event currently loaded in the form; null event_id = a new event.
  const [selectedId, setSelectedId] = useState<string | null>(initial?.event_id ?? null);
  const [draft, setDraft] = useState<RaceEventIn>(initial ? toDraft(initial) : EMPTY);
  const [timeStr, setTimeStr] = useState(secToHMS(initial?.target_finish_time_sec));
  const [subInput, setSubInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A saved event the athlete is being asked about as a sub event.
  const [subAsk, setSubAsk] = useState<RaceEvent | null>(null);

  // Esc cancels, Ctrl+Enter saves — mirrors the buttons' disabled={busy}.
  // While the sub-event question is open, Esc is "Not now" and Ctrl+Enter adds.
  useModalKeys(
    () => { if (!busy) onClose(); },
    () => { if (!busy) (subAsk ? addAsSubEvent() : save()); },
  );

  async function addAsSubEvent() {
    if (!subAsk) return;
    setBusy(true);
    setError(null);
    try {
      await api.addSeasonSubEvent(subAsk.event_id);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function toDraft(g: RaceEvent): RaceEventIn {
    const { event_id: _omit, ...rest } = g;
    return { ...EMPTY, ...rest };
  }

  function selectEvent(g: RaceEvent | null) {
    setError(null);
    if (g) {
      setSelectedId(g.event_id);
      setDraft(toDraft(g));
      setTimeStr(secToHMS(g.target_finish_time_sec));
    } else {
      setSelectedId(null);
      setDraft(EMPTY);
      setTimeStr("");
    }
    setSubInput("");
  }

  function set<K extends keyof RaceEventIn>(key: K, value: RaceEventIn[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  const participating = isParticipating(draft);
  // Judged on the draft's date, so moving a finished event forward unlocks it.
  const past = isPastEvent(draft);

  function addSubGoal() {
    const v = subInput.trim();
    if (!v) return;
    set("sub_goals", [...draft.sub_goals, v]);
    setSubInput("");
  }

  async function save(thenPlan = false) {
    if (!draft.name.trim()) return setError("Title is required.");
    if (!draft.race_date) return setError("Race date is required.");
    setBusy(true);
    setError(null);
    const payload: RaceEventIn = {
      ...draft,
      name: draft.name.trim(),
      location_name: draft.location_name?.trim() || null,
      target_finish_time_sec: hmsToSec(timeStr),
      website: draft.website?.trim() || null,
      notes: draft.notes?.trim() || null,
    };
    try {
      const saved = selectedId ? await api.updateEvent(selectedId, payload) : await api.createEvent(payload);
      onSaved();
      const prevDate = selectedId ? events.find((g) => g.event_id === selectedId)?.race_date : undefined;
      if (!thenPlan && subEventCandidate(seasonPlan, saved, prevDate)) {
        setSubAsk(saved);
        return;
      }
      onClose();
      if (thenPlan) onPlan?.(saved.event_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteEvent(selectedId);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (subAsk && seasonPlan?.event) {
    const btn: React.CSSProperties = { cursor: "pointer", fontSize: 12.5, fontWeight: 600, padding: "9px 16px", borderRadius: 6, border: "none" };
    return (
      <div onClick={() => !busy && onClose()} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50, padding: 24 }}>
        <div onClick={(e) => e.stopPropagation()} style={{ width: 460, maxWidth: "100%", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}>
          <div style={{ padding: "13px 18px", borderTop: `3px solid ${C.form}`, borderBottom: `1px solid ${C.border}`, background: C.bar, fontSize: 14, fontWeight: 700, color: C.text }}>
            Add as a sub event?
          </div>
          <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={{ fontSize: 13, color: C.text, lineHeight: 1.5 }}>
              {subAsk.name} is before {seasonPlan.event.name}. Add it as a sub event of your season plan?
            </span>
            <span style={{ fontSize: 12, color: C.muted, lineHeight: 1.5 }}>
              The plan eases off around it, by its distance, and {seasonPlan.event.name} stays the goal of every week.
            </span>
            {error && <span style={{ fontSize: 12, color: C.red }}>{error}</span>}
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "12px 18px", borderTop: `1px solid ${C.border}`, background: C.bar }}>
            <button onClick={onClose} disabled={busy} style={{ ...btn, background: "transparent", color: C.muted, border: `1px solid ${C.border}` }}>Not now</button>
            <button onClick={addAsSubEvent} disabled={busy} style={{ ...btn, background: C.form, color: "#fff", opacity: busy ? 0.5 : 1 }}>
              {busy ? "Adding..." : "Add sub event"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50, padding: 24 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: 760, maxWidth: "100%", maxHeight: "90vh", display: "flex", flexDirection: "column", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "13px 18px", borderBottom: `1px solid ${C.border}`, background: C.bar }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Manage events</span>
          <span onClick={onClose} style={{ cursor: "pointer", fontSize: 18, color: C.muted, lineHeight: 1 }}>✕</span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "210px 1fr", flex: 1, minHeight: 0 }}>
          {/* Event list */}
          <div style={{ borderRight: `1px solid ${C.border}`, overflowY: "auto", padding: 8, display: "flex", flexDirection: "column", gap: 4 }}>
            {events.map((g) => {
              // Shown as "not racing" only while it still could be a race: a past
              // event is already faded and its switch is locked.
              const notRacing = !isParticipating(g) && !isPastEvent(g);
              return (
                <div
                  key={g.event_id}
                  onClick={() => selectEvent(g)}
                  style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", borderRadius: 5, background: g.event_id === selectedId ? C.inner2 : "transparent", border: `1px solid ${g.event_id === selectedId ? C.cardBorder : "transparent"}`, opacity: isPastEvent(g) ? PAST_EVENT_OPACITY : notRacing ? NOT_RACING_OPACITY : 1 }}
                >
                  <span style={{ fontSize: 16 }}>{g.icon || "🏆"}</span>
                  <div style={{ minWidth: 0, lineHeight: 1.25 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: C.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{g.name || "Untitled"}</div>
                    <div style={{ fontSize: 10, color: C.muted }}>{(g.race_date || "no date") + (notRacing ? " · not racing" : "")}</div>
                  </div>
                </div>
              );
            })}
            <div
              onClick={() => selectEvent(null)}
              style={{ cursor: "pointer", padding: "8px 10px", borderRadius: 5, fontSize: 12, fontWeight: 600, color: C.form, border: `1px dashed ${C.cardBorder}`, textAlign: "center", marginTop: 4, background: selectedId === null ? C.inner2 : "transparent" }}
            >
              + New event
            </div>
          </div>

          {/* Form */}
          <div style={{ overflowY: "auto", padding: 18, display: "flex", flexDirection: "column", gap: 14 }}>
            <Field label="Icon">
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {ICONS.map((ic) => (
                  <span
                    key={ic}
                    onClick={() => set("icon", ic)}
                    style={{ cursor: "pointer", fontSize: 18, width: 34, height: 34, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 6, background: draft.icon === ic ? C.form : C.inner, border: `1px solid ${draft.icon === ic ? C.form : C.cardBorder}` }}
                  >
                    {ic}
                  </span>
                ))}
              </div>
            </Field>

            <Field label="Title">
              <input value={draft.name} onChange={(e) => set("name", e.target.value)} placeholder="e.g. Ironman 70.3 Vichy" style={inputStyle} />
            </Field>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Distance">
                <select value={draft.type} onChange={(e) => set("type", e.target.value)} style={inputStyle}>
                  {EVENT_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </Field>
              <Field label="Race date">
                {/* colorScheme dark makes the native calendar icon/picker render light */}
                <input type="date" value={draft.race_date} onChange={(e) => set("race_date", e.target.value)} style={{ ...inputStyle, colorScheme: "dark" }} />
              </Field>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Location">
                <input value={draft.location_name ?? ""} onChange={(e) => set("location_name", e.target.value)} placeholder="City (for weather)" style={inputStyle} />
              </Field>
              <Field label="Target finish (h:mm:ss)">
                <input value={timeStr} onChange={(e) => setTimeStr(e.target.value)} placeholder="5:30:00" style={inputStyle} />
              </Field>
            </div>

            <Field label="Website">
              <input value={draft.website ?? ""} onChange={(e) => set("website", e.target.value)} placeholder="https://race-event.com" style={inputStyle} />
            </Field>

            <Field label="Sub-goals">
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: draft.sub_goals.length ? 8 : 0 }}>
                {draft.sub_goals.map((s, i) => (
                  <span key={i} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "#dfe0e6", background: C.inner2, border: `1px solid ${C.cardBorder}`, borderRadius: 14, padding: "4px 6px 4px 11px" }}>
                    {s}
                    <span onClick={() => set("sub_goals", draft.sub_goals.filter((_, j) => j !== i))} style={{ cursor: "pointer", color: C.muted, fontSize: 13, lineHeight: 1 }}>✕</span>
                  </span>
                ))}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  value={subInput}
                  onChange={(e) => setSubInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSubGoal(); } }}
                  placeholder="e.g. Swim sub-35:00"
                  style={inputStyle}
                />
                <button onClick={addSubGoal} style={{ ...btnStyle, background: C.inner2, color: C.text }}>Add</button>
              </div>
            </Field>

            <Field label="Notes">
              <textarea value={draft.notes ?? ""} onChange={(e) => set("notes", e.target.value)} rows={2} placeholder="Anything the coach should know" style={{ ...inputStyle, resize: "vertical", fontFamily: "inherit" }} />
            </Field>

            {error && <div style={{ fontSize: 12, color: C.red }}>{error}</div>}
          </div>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 18px", borderTop: `1px solid ${C.border}`, background: C.bar }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            {selectedId && (
              <button onClick={remove} disabled={busy} style={{ ...btnStyle, background: "transparent", color: C.red, border: `1px solid ${C.cardBorder}` }}>Delete</button>
            )}
            {/* Wrapping label: clicking the text flips the switch too. A finished
                event is locked -- participation only matters for one still to come. */}
            <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: past ? "not-allowed" : "pointer", opacity: past ? 0.4 : 1 }}>
              <button
                type="button"
                role="switch"
                aria-checked={participating}
                disabled={past}
                onClick={() => set("participating", !participating)}
                className="participation-switch"
                style={{ position: "relative", flex: "none", width: 38, height: 22, padding: 0, border: "none", borderRadius: 11, background: participating ? C.form : "#3a3d44", cursor: "inherit" }}
              >
                <span style={{ position: "absolute", top: 3, left: participating ? 19 : 3, width: 16, height: 16, borderRadius: 8, background: "#fff", transition: "left 150ms ease" }} />
              </button>
              <span style={{ fontSize: 13, fontWeight: 600, color: participating ? C.text : C.muted }}>{participating ? "Participating" : "Not participating"}</span>
            </label>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={onClose} disabled={busy} style={{ ...btnStyle, background: "transparent", color: C.textChip, border: `1px solid ${C.cardBorder}` }}>Cancel</button>
            {/* Only an upcoming event the athlete is racing can carry a season plan. */}
            {onPlan && participating && !past && (
              <button onClick={() => save(true)} disabled={busy} style={{ ...btnStyle, background: C.inner2, color: C.text, border: `1px solid ${C.cardBorder}` }}>Save and plan</button>
            )}
            <button onClick={() => save()} disabled={busy} style={{ ...btnStyle, background: C.form, color: "#fff" }}>{busy ? "Saving…" : "Save event"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <span style={{ fontSize: 10, letterSpacing: ".06em", textTransform: "uppercase", color: C.muted }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle: React.CSSProperties = {
  flex: 1,
  width: "100%",
  background: C.bar,
  border: `1px solid ${C.cardBorder}`,
  borderRadius: 6,
  padding: "9px 11px",
  fontSize: 12.5,
  color: C.text,
  outline: "none",
  boxSizing: "border-box",
};

const btnStyle: React.CSSProperties = {
  cursor: "pointer",
  fontSize: 12.5,
  fontWeight: 600,
  padding: "9px 16px",
  borderRadius: 6,
  border: "none",
};
