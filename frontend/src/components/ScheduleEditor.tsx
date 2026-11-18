// The athlete's training week, edited day by day.
//
// Tabs plus a detail panel keep the modal short, so each tab carries its own
// state underneath the day name — discipline dots in the dashboard's discipline
// colours, or a dash for a rest day. The strip therefore still reads as the
// whole week while only one day is expanded.
//
// Tabs are navigation and the controls inside are values, so the two look
// deliberately different: a tab is a flat panel with an underline, a control is
// a filled toggle. Clicking a tab must never look like it sets something.

import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { C, DISCIPLINE_COLOR } from "../theme";
import type { DaySchedule, SessionType, Weekday, WeeklySchedule } from "../types";
import { Field, Hint, ToggleButton, inputStyle } from "./settingsControls";

const DAYS: { key: Weekday; label: string; short: string }[] = [
  { key: "monday", label: "Monday", short: "Mon" },
  { key: "tuesday", label: "Tuesday", short: "Tue" },
  { key: "wednesday", label: "Wednesday", short: "Wed" },
  { key: "thursday", label: "Thursday", short: "Thu" },
  { key: "friday", label: "Friday", short: "Fri" },
  { key: "saturday", label: "Saturday", short: "Sat" },
  { key: "sunday", label: "Sunday", short: "Sun" },
];

const DISCIPLINES = [
  { key: "swim", label: "Swim" },
  { key: "bike", label: "Bike" },
  { key: "run", label: "Run" },
  { key: "strength", label: "Strength" },
];

const SESSION_TYPES: { key: SessionType; label: string }[] = [
  { key: "any", label: "Any" },
  { key: "endurance", label: "Endurance" },
  { key: "quality", label: "Quality" },
  { key: "long", label: "Long" },
  { key: "brick", label: "Brick" },
];

// The one cross-field rule the editor enforces before save: a brick day needs
// at least two disciplines to alternate between. SettingsModal calls this and
// blocks the save with the message; the backend 422s as a backstop.
export function scheduleProblem(value: WeeklySchedule): string | null {
  for (const d of DAYS) {
    const s = value.schedule[d.key];
    if (!s.rest && s.session_type === "brick" && s.preferred_disciplines.length < 2) {
      return `${d.label} is a brick day — pick at least two disciplines for it.`;
    }
  }
  return null;
}

const TRAINING_DAY: DaySchedule = {
  available: true,
  rest: false,
  preferred_disciplines: [],
  max_sessions: 1,
  session_type: "any",
};

const REST_DAY: DaySchedule = {
  available: false,
  rest: true,
  preferred_disciplines: [],
  max_sessions: 1,
  session_type: "any",
};

function orderedDays(weekStart: 0 | 1) {
  return weekStart === 1 ? DAYS : [DAYS[6], ...DAYS.slice(0, 6)];
}

export function ScheduleEditor({
  value,
  onChange,
  weekStart,
}: {
  value: WeeklySchedule;
  onChange: (next: WeeklySchedule) => void;
  weekStart: 0 | 1;
}) {
  const days = orderedDays(weekStart);
  const [selected, setSelected] = useState<Weekday>(days[0].key);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const day = value.schedule[selected];

  const setDay = (key: Weekday, patch: Partial<DaySchedule>) =>
    onChange({
      ...value,
      schedule: { ...value.schedule, [key]: { ...value.schedule[key], ...patch } },
    });

  const toggleDiscipline = (d: string) => {
    const has = day.preferred_disciplines.includes(d);
    setDay(selected, {
      preferred_disciplines: has
        ? day.preferred_disciplines.filter((x) => x !== d)
        : [...day.preferred_disciplines, d],
    });
  };

  const onTabKey = (e: KeyboardEvent) => {
    const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    const i = days.findIndex((d) => d.key === selected);
    const next = days[(i + delta + days.length) % days.length].key;
    setSelected(next);
    tabRefs.current[next]?.focus();
  };

  // The week limits shown below mirror the backend's derivation live: they
  // fall out of the rest days, never edited directly.
  const openFlags = DAYS.map((d) => !value.schedule[d.key].rest);
  const trainingDayCount = openFlags.filter(Boolean).length;
  let consecutive = 7;
  if (!openFlags.every(Boolean)) {
    let best = 0;
    let cur = 0;
    for (const open of [...openFlags, ...openFlags]) {
      cur = open ? cur + 1 : 0; // doubled so a run of days may wrap the weekend
      if (cur > best) best = cur;
    }
    consecutive = best;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* Week strip — navigation, and an overview of every day at once. */}
      <div>
        <div
          role="tablist"
          aria-label="Training week"
          onKeyDown={onTabKey}
          style={{ display: "flex", gap: 4, borderBottom: `1px solid ${C.modalControlBorder}` }}
        >
          {days.map((d) => {
            const s = value.schedule[d.key];
            const active = d.key === selected;
            return (
              <button
                key={d.key}
                ref={(el) => {
                  tabRefs.current[d.key] = el;
                }}
                role="tab"
                aria-selected={active}
                tabIndex={active ? 0 : -1}
                onClick={() => setSelected(d.key)}
                title={d.label}
                style={{
                  flex: 1,
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 6,
                  padding: "10px 0 8px",
                  background: active ? C.modalInner : "transparent",
                  border: "none",
                  borderBottom: `2px solid ${active ? C.form : "transparent"}`,
                  borderRadius: "8px 8px 0 0",
                  marginBottom: -1,
                  fontSize: 13,
                  fontWeight: 600,
                  color: s.rest ? C.muted : active ? C.white : C.modalLabel,
                }}
              >
                <span>{d.short}</span>
                <DayMarks day={s} />
              </button>
            );
          })}
        </div>

        {/* Detail panel — the selected tab's contents. */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 16,
            marginTop: 12,
            padding: 16,
            background: C.bar,
            border: `1px solid ${C.modalControlBorder}`,
            borderRadius: 8,
          }}
        >
          <Field label={DAYS.find((d) => d.key === selected)!.label} gap={8}>
            <div style={{ display: "flex", gap: 10 }}>
              <ToggleButton
                active={!day.rest}
                onClick={() => setDay(selected, TRAINING_DAY)}
                label="Training"
                fontSize={14}
                padding="11px 0"
              />
              <ToggleButton
                active={day.rest}
                onClick={() => setDay(selected, REST_DAY)}
                label="Rest day"
                fontSize={14}
                padding="11px 0"
              />
            </div>
          </Field>

          {day.rest ? (
            <Hint>No sessions are ever planned on this day. The coach cannot override it.</Hint>
          ) : (
            <>
              <Field label="Preferred disciplines" gap={8}>
                <div style={{ display: "flex", gap: 8 }}>
                  {DISCIPLINES.map((d) => (
                    <ToggleButton
                      key={d.key}
                      active={day.preferred_disciplines.includes(d.key)}
                      onClick={() => toggleDiscipline(d.key)}
                      label={d.label}
                      fontSize={13}
                      padding="10px 0"
                      color={DISCIPLINE_COLOR[d.key]}
                    />
                  ))}
                </div>
                <Hint>
                  {day.preferred_disciplines.length === 0
                    ? "None selected — the coach picks the discipline for this day."
                    : "The coach follows these unless recovery calls for something else."}
                </Hint>
              </Field>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <Field label="Max sessions" gap={8}>
                  <div style={{ display: "flex", gap: 8 }}>
                    {[1, 2, 3].map((n) => (
                      <ToggleButton
                        key={n}
                        active={day.max_sessions === n}
                        onClick={() => setDay(selected, { max_sessions: n })}
                        label={String(n)}
                        fontSize={14}
                        padding="10px 0"
                        // A brick is at least two back-to-back sessions.
                        disabled={n === 1 && day.session_type === "brick"}
                      />
                    ))}
                  </div>
                </Field>
                <Field label="Session type" gap={8}>
                  <select
                    value={day.session_type}
                    onChange={(e) => {
                      const t = e.target.value as SessionType;
                      setDay(selected, {
                        session_type: t,
                        ...(t === "brick" && day.max_sessions < 2 ? { max_sessions: 2 } : {}),
                      });
                    }}
                    style={{ ...inputStyle, padding: "10px 12px", fontSize: 14 }}
                  >
                    {SESSION_TYPES.map((t) => (
                      <option
                        key={t.key}
                        value={t.key}
                        // Brick needs two disciplines to alternate between.
                        disabled={t.key === "brick" && day.preferred_disciplines.length < 2}
                      >
                        {t.label}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              {day.session_type === "brick" &&
                (day.preferred_disciplines.length < 2 ? (
                  <Hint color={C.yellow}>
                    A brick day needs at least two disciplines — pick the ones to go
                    back-to-back.
                  </Hint>
                ) : (
                  <Hint>
                    The coach must plan the selected disciplines back-to-back on this day
                    (at least two sessions).
                  </Hint>
                ))}
            </>
          )}
        </div>
      </div>

      {/* Week limits — read-only, derived from the days above. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: C.modalLabel }}>Week limits</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <WeekStat label="Max training days / week" value={trainingDayCount} />
          <WeekStat label="Max consecutive days" value={consecutive} />
        </div>
        <Hint>
          Calculated from the week — mark days as rest to lower them. Long rides, long runs and
          bricks follow each day's session type and disciplines.
        </Hint>
      </div>
    </div>
  );
}

// A derived number the athlete reads but never types: input-shaped so the
// grid lines up, recessed so it doesn't invite a click.
function WeekStat({ label, value }: { label: string; value: number }) {
  return (
    <Field label={label}>
      <div
        style={{
          ...inputStyle,
          background: C.modalInner,
          color: C.mutedBright2,
          fontWeight: 600,
        }}
      >
        {value}
      </div>
    </Field>
  );
}

// A day's shape at a glance: one dot per preferred discipline, a dash for rest,
// a neutral dot for a training day with no preference.
function DayMarks({ day }: { day: DaySchedule }) {
  if (day.rest) {
    return (
      <span
        style={{ display: "block", width: 8, height: 2, background: C.muted, borderRadius: 1 }}
      />
    );
  }
  const marks = day.preferred_disciplines.length ? day.preferred_disciplines : ["_any"];
  return (
    <span style={{ display: "flex", gap: 3, height: 5, alignItems: "center" }}>
      {marks.map((d) => (
        <span
          key={d}
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: DISCIPLINE_COLOR[d] ?? C.muted3,
          }}
        />
      ))}
    </span>
  );
}

