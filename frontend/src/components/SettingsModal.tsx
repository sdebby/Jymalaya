import { useEffect, useState } from "react";
import { C } from "../theme";
import { api } from "../api";
import { ageFromBirthday, pace } from "../format";
import { clearChat } from "../chatStore";
import { clearSettings, clearWelcomeSeen, WEEKDAYS, type AppSettings } from "../settings";
import { useModalKeys } from "../useModalKeys";
import { CloseButton, Divider, ModalBody, ModalScrim, ModalShell } from "./SessionModal";
import { ScheduleEditor, scheduleProblem } from "./ScheduleEditor";
import { Field, Hint, Note, ToggleButton, inputStyle } from "./settingsControls";
import type { AthleteProfileIn, WeeklySchedule } from "../types";

// The numeric profile fields, in display order. Values are edited as strings and
// parsed on save so partial input doesn't fight the keyboard. The birthday is
// edited separately (a date, not a number) and rendered after these, and so is
// the activity class: it is a bounded 0-10 slider whose 0 the loop below would
// reject as "greater than 0".
type NumericField = Exclude<keyof AthleteProfileIn, "birthday" | "activity_class">;

// min/max/step are per field because the thresholds are whole units while the
// VO2max one is tenths; omitting them keeps a field on the whole-number default
// the five thresholds have always used.
const PROFILE_FIELDS: {
  key: NumericField;
  label: string;
  min?: number;
  max?: number;
  step?: number;
  hint?: (v: number) => string;
}[] = [
  { key: "ftp_w", label: "Bike FTP (W)" },
  { key: "lthr_bpm", label: "LTHR (bpm)" },
  { key: "run_threshold_pace_sec_km", label: "Run threshold (sec/km)", hint: (v) => `${pace(v)} min/km` },
  { key: "css_sec_100m", label: "Critical swim speed (sec/100m)", hint: (v) => `${pace(v)} /100m` },
  { key: "weight_kg", label: "Weight (kg)" },
  {
    key: "vo2max_decline_threshold",
    label: "VO2max decline threshold (ml/kg/min)",
    // 0.1 is effectively "any drop": every reading, weekly average and the
    // change itself are rounded to one decimal, so no smaller drop exists.
    min: 0.1,
    max: 5,
    step: 0.1,
    hint: (v) => `a 4-week drop of ${v} or more asks for a VO2max session`,
  },
];

type ProfileDraft = Record<keyof AthleteProfileIn, string>;

// Today, as the date input's max — a birthday in the future is refused by the
// backend, so don't let the picker offer one.
const todayISO = (): string => {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

// Blue -> green ramp for the activity-class slider. The design carries no
// gradient tokens, so interpolate between the two accent colours.
const mixHex = (a: string, b: string, t: number): string => {
  const ch = (h: string, i: number) => parseInt(h.slice(1 + i * 2, 3 + i * 2), 16);
  const v = [0, 1, 2].map((i) => Math.round(ch(a, i) + (ch(b, i) - ch(a, i)) * t));
  return `#${v.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
};

// The schedule's rest days drive the dashboard's recovery-day rendering, so
// derive the local weekday numbers (0 = Sunday) from it rather than storing
// them twice.
const restDayNums = (s: WeeklySchedule): number[] =>
  WEEKDAYS.filter((w) => s.schedule[w.label.toLowerCase() as keyof WeeklySchedule["schedule"]]?.rest)
    .map((w): number => w.num);

export function SettingsModal({
  settings,
  onClose,
  onSave,
  onDataDeleted,
  onRecalculated,
  onInstructionsSaved,
}: {
  settings: AppSettings;
  onClose: () => void;
  onSave: (s: AppSettings) => void;
  onDataDeleted: () => void;
  onRecalculated?: () => void;
  // After new coach instructions are saved: the season plan's note, if it read them.
  onInstructionsSaved?: (seasonPlanNote: string | null) => void;
}) {
  const [draft, setDraft] = useState<AppSettings>(settings);
  const [profile, setProfile] = useState<ProfileDraft | null>(null);
  // Last saved profile values — the "Re-calculate" button shows only while
  // the draft differs from these.
  const [profileSaved, setProfileSaved] = useState<ProfileDraft | null>(null);
  const [profileState, setProfileState] = useState<"loading" | "ready" | "offline">("loading");
  const [recalcBusy, setRecalcBusy] = useState(false);
  const [recalcNote, setRecalcNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The training week lives on the backend (it shapes the AI plan); the local
  // recoveryDays copy is derived from it for the dashboard's own rendering.
  const [sched, setSched] = useState<WeeklySchedule | null>(null);
  const [schedSaved, setSchedSaved] = useState<WeeklySchedule | null>(null);
  const [schedState, setSchedState] = useState<"loading" | "ready" | "offline">("loading");
  // Custom coach instructions: fetched lazily when the section is opened.
  const [instrOpen, setInstrOpen] = useState(false);
  const [instrState, setInstrState] = useState<"idle" | "loading" | "ready" | "offline">("idle");
  const [instructions, setInstructions] = useState("");
  const [instrSaved, setInstrSaved] = useState("");
  // Delete-everything flow: idle -> armed (first click) -> deleting.
  const [deleteState, setDeleteState] = useState<"idle" | "armed" | "deleting">("idle");

  // Esc cancels, Ctrl+Enter saves (save() guards busy itself).
  useModalKeys(onClose, save);

  useEffect(() => {
    let alive = true;
    api
      .athlete()
      .then((a) => {
        if (!alive) return;
        const p = {
          ...Object.fromEntries(PROFILE_FIELDS.map((f) => [f.key, String(a[f.key])])),
          // Profiles saved before the activity class existed carry no value; the
          // backend's default is 4, so fall back to the same number here.
          activity_class: String(a.activity_class ?? 4),
          birthday: a.birthday ?? "",
        } as ProfileDraft;
        setProfile(p);
        setProfileSaved(p);
        setProfileState("ready");
      })
      .catch(() => alive && setProfileState("offline"));
    // The backend's schedule is the truth; sync the draft's recovery days to it
    // so the dashboard marks the same days the coach treats as rest.
    api
      .schedule()
      .then((s) => {
        if (!alive) return;
        setSched(s);
        setSchedSaved(s);
        setDraft((d) => ({ ...d, recoveryDays: restDayNums(s) }));
        setSchedState("ready");
      })
      .catch(() => alive && setSchedState("offline"));
    return () => {
      alive = false;
    };
  }, []);

  function toggleInstructions() {
    setInstrOpen((o) => !o);
    if (instrState !== "idle") return;
    setInstrState("loading");
    api
      .coachInstructions()
      .then((r) => {
        setInstructions(r.text);
        setInstrSaved(r.text);
        setInstrState("ready");
      })
      .catch(() => setInstrState("offline"));
  }

  async function deleteEverything() {
    if (deleteState === "idle") {
      setDeleteState("armed");
      return;
    }
    if (deleteState !== "armed") return;
    setDeleteState("deleting");
    setError(null);
    try {
      await api.deleteAllData();
    } catch (e) {
      setError(`Couldn't delete the backend data: ${e instanceof Error ? e.message : e}`);
      setDeleteState("idle");
      return;
    }
    // Backend is wiped — clear the browser-side data too.
    clearChat();
    clearSettings();
    clearWelcomeSeen();
    onDataDeleted();
    onClose();
  }

  // Validate the profile draft; sets the error message and returns null on a
  // bad field.
  function parseProfile(): AthleteProfileIn | null {
    if (!profile) return null;
    const parsed = {} as AthleteProfileIn;
    for (const f of PROFILE_FIELDS) {
      const v = Number(profile[f.key]);
      if (!isFinite(v) || v <= 0) {
        setError(`${f.label} must be a number greater than 0.`);
        return null;
      }
      // The backend bounds this field too, so catch it here and say which
      // field it was rather than surfacing a bare 422.
      if (f.max != null && v > f.max) {
        setError(`${f.label} must be ${f.max} or less.`);
        return null;
      }
      parsed[f.key] = v;
    }
    const activity = Math.round(Number(profile.activity_class));
    if (!isFinite(activity) || activity < 0 || activity > 10) {
      setError("Activity class must be between 0 and 10.");
      return null;
    }
    parsed.activity_class = activity;
    if (ageFromBirthday(profile.birthday) == null) {
      setError("Birthday must be a real date in the past.");
      return null;
    }
    parsed.birthday = profile.birthday;
    return parsed;
  }

  // Age is derived, never typed: the backend recomputes it from the birthday on
  // every read, and this mirrors that as a live hint under the date input.
  const birthdayAge = profile ? ageFromBirthday(profile.birthday) : null;

  // The slider is bounded, so clamp whatever the draft holds rather than let a
  // stray value paint the track past its ends.
  const activityClass = profile
    ? Math.min(10, Math.max(0, Math.round(Number(profile.activity_class)) || 0))
    : 4;
  const activityColor = mixHex(C.form, C.fit, activityClass / 10);

  const profileDirty =
    profileState === "ready" &&
    profile != null &&
    profileSaved != null &&
    (Object.keys(profileSaved) as (keyof ProfileDraft)[]).some(
      (k) => profile[k] !== profileSaved[k],
    );

  // Save the edited thresholds, then recompute TSS for the last 7 days of
  // stored workouts from them (plus the daily load series, full history).
  async function recalculate() {
    if (busy || recalcBusy) return;
    setError(null);
    const parsed = parseProfile();
    if (!parsed) return;
    setRecalcBusy(true);
    setRecalcNote(null);
    try {
      await api.updateAthlete(parsed);
      const r = await api.recomputeMetrics(7);
      setProfileSaved(profile);
      setRecalcNote(
        `Thresholds saved — ${r.rewritten} of ${r.considered} workout${r.considered === 1 ? "" : "s"} in the last 7 days updated.`,
      );
      onRecalculated?.();
    } catch (e) {
      setError(`Couldn't recalculate: ${e instanceof Error ? e.message : e}`);
    } finally {
      setRecalcBusy(false);
    }
  }

  async function save() {
    if (busy) return;
    setError(null);
    if (profileState === "ready" && profile) {
      const parsed = parseProfile();
      if (!parsed) return;
      setBusy(true);
      try {
        await api.updateAthlete(parsed);
      } catch (e) {
        setError(`Couldn't save the athlete profile: ${e instanceof Error ? e.message : e}`);
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    let recoveryDays = draft.recoveryDays;
    if (schedState === "ready" && sched && JSON.stringify(sched) !== JSON.stringify(schedSaved)) {
      const problem = scheduleProblem(sched);
      if (problem) {
        setError(problem);
        return;
      }
      setBusy(true);
      try {
        const stored = await api.updateSchedule(sched);
        recoveryDays = restDayNums(stored);
      } catch (e) {
        setError(`Couldn't save the training schedule: ${e instanceof Error ? e.message : e}`);
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    if (instrState === "ready" && instructions.trim() !== instrSaved) {
      setBusy(true);
      try {
        const saved = await api.updateCoachInstructions(instructions.trim());
        onInstructionsSaved?.(saved.season_plan_note ?? null);
      } catch (e) {
        setError(`Couldn't save the coach instructions: ${e instanceof Error ? e.message : e}`);
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    onSave({ ...draft, recoveryDays });
    onClose();
  }

  return (
    <ModalScrim onClose={onClose}>
      <ModalShell>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "20px 26px", borderBottom: `1px solid ${C.modalBorder}`, background: C.bar, flex: "none" }}>
          <span style={{ fontSize: 17, fontWeight: 700, color: C.white }}>Settings</span>
          <CloseButton onClose={onClose} />
        </div>

        <ModalBody gap={32}>
          {/* ── Athlete profile ─────────────────────────────────────── */}
          <SettingsSection
            title="Athlete profile"
            description="Thresholds used for TSS and the predicted workouts — bike FTP drives the watt targets."
          >
            {profileState === "loading" && <Note>Loading profile…</Note>}
            {profileState === "offline" && (
              <Note>Backend unreachable — the athlete profile can't be edited right now.</Note>
            )}
            {profileState === "ready" && profile && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                {PROFILE_FIELDS.map((f) => {
                  const num = Number(profile[f.key]);
                  return (
                    <Field key={f.key} label={f.label}>
                      <input
                        type="number"
                        min={f.min ?? 1}
                        max={f.max}
                        step={f.step}
                        value={profile[f.key]}
                        onChange={(e) => setProfile((p) => (p ? { ...p, [f.key]: e.target.value } : p))}
                        style={inputStyle}
                      />
                      {f.hint && (
                        <span style={{ fontSize: 12, color: C.successBright }}>
                          {isFinite(num) && num > 0 ? `= ${f.hint(num)}` : " "}
                        </span>
                      )}
                    </Field>
                  );
                })}
                <Field label="Birthday">
                  <input
                    type="date"
                    max={todayISO()}
                    value={profile.birthday}
                    onChange={(e) => setProfile((p) => (p ? { ...p, birthday: e.target.value } : p))}
                    // colorScheme keeps the native date picker and its icon dark.
                    style={{ ...inputStyle, colorScheme: "dark" }}
                  />
                  <span style={{ fontSize: 12, color: C.successBright }}>
                    {birthdayAge != null ? `= ${birthdayAge} years old` : " "}
                  </span>
                </Field>
                {/* Full width: a slider reads badly in a half-width column. */}
                <div style={{ gridColumn: "1 / -1" }}>
                  <Field label="Activity class">
                    <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                      <input
                        type="range"
                        min={0}
                        max={10}
                        step={1}
                        className="activity-slider"
                        value={String(activityClass)}
                        onChange={(e) =>
                          setProfile((p) => (p ? { ...p, activity_class: e.target.value } : p))
                        }
                        // The element paints its own track (the pseudo-element
                        // track in index.css is transparent): the filled part
                        // ramps blue to the current colour, the rest stays
                        // neutral, and the thumb takes that colour too.
                        style={
                          {
                            flex: 1,
                            background: `linear-gradient(90deg, ${C.form} 0%, ${activityColor} ${activityClass * 10}%, ${C.modalInner} ${activityClass * 10}%)`,
                            ["--thumb" as string]: activityColor,
                          } as React.CSSProperties
                        }
                      />
                      <span style={{ fontSize: 12, color: C.successBright, minWidth: 16, textAlign: "right" }}>
                        {activityClass}
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: C.mutedBright }}>
                      <span>Inactive</span>
                      <span>Competitive</span>
                    </div>
                  </Field>
                </div>
              </div>
            )}
            {profileDirty && (
              <Hint>
                Re-calculate (below) saves these thresholds and recalculates TSS for the
                workouts of the last 7 days.
              </Hint>
            )}
            {recalcNote && !profileDirty && (
              <span style={{ fontSize: 12, color: C.successBright, lineHeight: 1.5 }}>{recalcNote}</span>
            )}
          </SettingsSection>

          <Divider />

          {/* ── Training schedule ───────────────────────────────────── */}
          <SettingsSection
            title="Training schedule"
            description="What the AI coach is allowed to plan, day by day — applied on the next plan generation."
          >
            {schedState === "ready" && sched ? (
              <ScheduleEditor value={sched} onChange={setSched} weekStart={draft.weekStart} />
            ) : (
              <Note>
                {schedState === "loading"
                  ? "Loading the training schedule..."
                  : "Backend unreachable — the training schedule can't be edited right now."}
              </Note>
            )}
          </SettingsSection>

          <Divider />

          {/* ── AI coach ────────────────────────────────────────────── */}
          <SettingsSection title="AI coach">
            <button
              onClick={toggleInstructions}
              style={{
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "13px 16px",
                borderRadius: 8,
                fontSize: 14,
                fontWeight: 600,
                color: C.modalLabel,
                background: C.modalInner,
                border: `1px solid ${C.modalControlBorder}`,
              }}
            >
              Custom instructions <span style={{ color: C.muted }}>{instrOpen ? "▾" : "›"}</span>
            </button>
            {instrOpen && instrState === "loading" && <Note>Loading instructions…</Note>}
            {instrOpen && instrState === "offline" && (
              <Note>Backend unreachable — the coach instructions can't be edited right now.</Note>
            )}
            {instrOpen && instrState === "ready" && (
              <>
                <textarea
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  rows={5}
                  maxLength={4000}
                  placeholder="e.g. bricks only on Saturday; swim on Tuesdays; max one swim per week"
                  style={{ ...inputStyle, fontSize: 14, resize: "vertical", fontFamily: "inherit", lineHeight: 1.5 }}
                />
                <Hint>
                  Standing preferences the AI coach follows when it plans your week —
                  applied the next time a plan is generated (on sync). Leave empty to
                  send no custom instructions.
                </Hint>
              </>
            )}
          </SettingsSection>

          <Divider />

          {/* ── Display ─────────────────────────────────────────────── */}
          <SettingsSection title="Display">
            <Field label="Week starts on">
              <div style={{ display: "flex", gap: 10 }}>
                {([{ v: 0, label: "Sunday" }, { v: 1, label: "Monday" }] as const).map((o) => (
                  <ToggleButton
                    key={o.v}
                    active={draft.weekStart === o.v}
                    onClick={() => setDraft((d) => ({ ...d, weekStart: o.v }))}
                    label={o.label}
                    fontSize={14}
                    padding="12px 0"
                  />
                ))}
              </div>
            </Field>
          </SettingsSection>

          <Divider />

          {/* ── Danger zone ─────────────────────────────────────────── */}
          <SettingsSection title="Danger zone" titleColor={C.red}>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, border: `1px solid ${C.red}55`, borderRadius: 10, padding: "16px 18px", background: `${C.red}11` }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 14, fontWeight: 700, color: C.white }}>Delete all data</span>
                <span style={{ fontSize: 12.5, color: C.dangerText, lineHeight: 1.5 }}>
                  Permanently wipes workouts, metrics, events, generated plans and the coach chat.
                  Config (athlete profile, schedule) is kept. Cannot be undone; re-sync from Garmin to start over.
                </span>
              </div>
              <button
                onClick={deleteEverything}
                disabled={deleteState === "deleting"}
                style={{
                  ...btnStyle,
                  alignSelf: "flex-start",
                  fontSize: 13.5,
                  padding: "11px 18px",
                  background: deleteState === "idle" ? "transparent" : C.red,
                  color: deleteState === "idle" ? C.red : "#fff",
                  border: `1px solid ${C.red}`,
                  cursor: deleteState === "deleting" ? "default" : "pointer",
                  opacity: deleteState === "deleting" ? 0.7 : 1,
                }}
              >
                {deleteState === "idle" && "Delete all data"}
                {deleteState === "armed" && "Click again to confirm"}
                {deleteState === "deleting" && "Deleting…"}
              </button>
            </div>
            {deleteState === "armed" && (
              <span style={{ fontSize: 12, color: C.red, lineHeight: 1.5 }}>
                This really deletes everything — click the button again to proceed, or{" "}
                <span onClick={() => setDeleteState("idle")} style={{ cursor: "pointer", textDecoration: "underline" }}>cancel</span>.
              </span>
            )}
          </SettingsSection>
        </ModalBody>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, padding: "16px 26px", borderTop: `1px solid ${C.modalBorder}`, background: C.bar, flex: "none" }}>
          <span style={{ fontSize: 12, color: C.red, flex: 1 }}>{error ?? ""}</span>
          {profileDirty && (
            <button
              onClick={recalculate}
              disabled={recalcBusy}
              title="Saves the thresholds and recalculates TSS for the workouts of the last 7 days"
              style={{
                ...btnStyle,
                background: "transparent",
                color: C.form,
                border: `1px solid ${C.form}`,
                opacity: recalcBusy ? 0.7 : 1,
                cursor: recalcBusy ? "default" : "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {recalcBusy ? "Recalculating…" : "Re-calculate"}
            </button>
          )}
          <button onClick={onClose} style={{ ...btnStyle, background: "transparent", color: C.modalLabel, border: `1px solid ${C.modalControlBorder}` }}>Cancel</button>
          <button
            onClick={save}
            disabled={busy}
            style={{ ...btnStyle, background: C.form, color: "#fff", opacity: busy ? 0.7 : 1, cursor: busy ? "default" : "pointer" }}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </ModalShell>
    </ModalScrim>
  );
}

function SettingsSection({
  title,
  description,
  titleColor,
  children,
}: {
  title: string;
  description?: string;
  titleColor?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <div style={{ fontSize: 15, fontWeight: 700, color: titleColor ?? C.white }}>{title}</div>
        {description && (
          <div style={{ fontSize: 13, color: C.mutedBright2, lineHeight: 1.5, marginTop: 3 }}>{description}</div>
        )}
      </div>
      {children}
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  cursor: "pointer",
  fontSize: 14,
  fontWeight: 600,
  padding: "11px 20px",
  borderRadius: 8,
  border: "none",
};
