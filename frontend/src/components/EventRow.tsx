import { useState } from "react";
import { C } from "../theme";
import { WeatherIcon, skyFromPrecip } from "../charts/WeatherIcon";
import { hm } from "../format";
import { isParticipating, isPastEvent, NOT_RACING_OPACITY, PAST_EVENT_OPACITY } from "../derive";
import { EventModal } from "./EventModal";
import type { RaceEvent, SeasonPlan, Weather } from "../types";

// Whole days from local midnight today to the race date.
function daysTo(raceDate: string): number | null {
  const [y, m, d] = raceDate.split("-").map(Number);
  if (!y || !m || !d) return null;
  const race = new Date(y, m - 1, d).getTime();
  const today = new Date();
  const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  return Math.round((race - midnight) / 86400000);
}

// Short countdown for an event card: days inside the last two weeks, weeks beyond.
function countdownLabel(raceDate: string): string | null {
  const days = daysTo(raceDate);
  if (days == null) return null;
  if (days < 0) return "past";
  if (days === 0) return "today";
  if (days < 14) return `${days} day${days === 1 ? "" : "s"}`;
  const wks = Math.round(days / 7);
  return `${wks} wk${wks === 1 ? "" : "s"}`;
}

function normalizeUrl(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}

function Countdown({ raceDate }: { raceDate: string }) {
  const days = daysTo(raceDate);
  if (days == null) return null;
  const past = days < 0;
  const today = days === 0;
  const label = today ? "Race day!" : past ? `${Math.abs(days)} days ago` : "to go";
  const accent = today ? C.fit : past ? C.muted : C.fat;
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
      {!today && !past && (
        <>
          <span style={{ fontSize: 22, fontWeight: 800, color: accent, lineHeight: 1 }}>{days}</span>
          <span style={{ fontSize: 11, color: C.muted2 }}>
            day{days === 1 ? "" : "s"} {label}
            {days >= 14 && <span style={{ color: C.muted }}> · {Math.round(days / 7)} wks</span>}
          </span>
        </>
      )}
      {(today || past) && (
        <span style={{ fontSize: 13, fontWeight: 700, color: accent }}>{label}</span>
      )}
    </div>
  );
}

const chipStyle = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  background: "#181b1f",
  border: `1px solid ${C.cardBorder}`,
  borderRadius: 4,
  padding: "6px 11px",
} as const;

function WeatherChip({ weather }: { weather: Weather | null }) {
  if (!weather || weather.temp_c == null) return null;
  const label = weather.source === "forecast" ? "forecast" : "climate avg";
  return (
    <div style={{ ...chipStyle, padding: "5px 12px" }}>
      <WeatherIcon sky={skyFromPrecip(weather.precipitation_probability_pct)} />
      <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.15 }}>
        <span style={{ fontSize: 12, color: C.text, fontWeight: 600 }}>
          {Math.round(weather.temp_c)}°{" "}
          <span style={{ fontSize: 10, color: C.muted, fontWeight: 400 }}>{weather.location_name ?? ""}</span>
        </span>
        <span style={{ fontSize: 9, color: C.muted2 }}>
          {weather.wind_kmh != null ? `Wind ${Math.round(weather.wind_kmh)} km/h` : ""}
          {weather.precipitation_probability_pct != null ? ` · ${Math.round(weather.precipitation_probability_pct)}% rain` : ""}
          {` · ${label}`}
        </span>
      </div>
    </div>
  );
}

export function EventRow({
  primary,
  weather,
  events,
  onChanged,
  onPlan,
  seasonPlan,
}: {
  primary: RaceEvent | null;
  weather: Weather | null;
  events: RaceEvent[];
  onChanged: () => void;
  // Opens the season plan modal on a just-saved event.
  onPlan?: (eventId: string) => void;
  // Lets a new earlier event be offered as a sub event of the plan.
  seasonPlan?: SeasonPlan | null;
}) {
  // open === false closed; otherwise the event_id to edit, or null for a new event.
  const [open, setOpen] = useState<string | null | false>(false);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "stretch",
        gap: 14,
        padding: "12px 16px",
        background: C.bar,
        borderBottom: `1px solid ${C.border}`,
      }}
    >
      {open !== false && (
        <EventModal events={events} initialEventId={open} onClose={() => setOpen(false)} onSaved={onChanged} onPlan={onPlan} seasonPlan={seasonPlan} />
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 5, flex: "none", minWidth: 280, borderRight: `1px solid ${C.border}`, paddingRight: 18 }}>
        <span style={{ fontSize: 10, letterSpacing: ".07em", textTransform: "uppercase", color: C.muted }}>Primary Event</span>
        {primary ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 5, opacity: isPastEvent(primary) ? PAST_EVENT_OPACITY : 1 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 9 }}>
              {primary.icon && <span style={{ fontSize: 17, lineHeight: 1 }}>{primary.icon}</span>}
              <span style={{ fontSize: 17, fontWeight: 700, color: "#fff", lineHeight: 1 }}>{primary.name}</span>
            </div>
            <Countdown raceDate={primary.race_date} />
            <span style={{ fontSize: 10.5, color: C.muted2 }}>
              {[primary.location_name, primary.race_date, primary.type].filter(Boolean).join(" · ")}
              {primary.target_finish_time_sec ? ` · target ${hm(primary.target_finish_time_sec)}` : ""}
            </span>
            {primary.website && (
              <a
                href={normalizeUrl(primary.website)}
                target="_blank"
                rel="noopener noreferrer"
                style={{ fontSize: 10.5, color: C.form, textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4, width: "fit-content" }}
              >
                Event website
              </a>
            )}
            <div style={{ marginTop: 4 }}>
              <WeatherChip weather={weather} />
            </div>
          </div>
        ) : (
          <span style={{ fontSize: 12, color: C.muted2 }}>No event set — click “Edit” to add your race.</span>
        )}
      </div>

      {/* All events — horizontally scrollable */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: 10, letterSpacing: ".07em", textTransform: "uppercase", color: C.muted }}>
          All events {events.length > 0 && <span style={{ color: C.muted2 }}>· {events.length}</span>}
        </span>
        <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 4 }}>
          {events.map((g) => {
            const countdown = countdownLabel(g.race_date);
            const isPrimary = primary != null && g.event_id === primary.event_id;
            const past = isPastEvent(g);
            // Calendar-only: still listed (this is how it is switched back on)
            // but it carries no countdown and is never the primary.
            const notRacing = !isParticipating(g) && !past;
            return (
              <div
                key={g.event_id}
                onClick={() => setOpen(g.event_id)}
                title="Click to edit"
                style={{
                  cursor: "pointer",
                  flex: "none",
                  width: 188,
                  display: "flex",
                  flexDirection: "column",
                  gap: 5,
                  background: "#181b1f",
                  border: `1px solid ${isPrimary ? C.form : C.cardBorder}`,
                  borderRadius: 5,
                  padding: "9px 11px",
                  opacity: past ? PAST_EVENT_OPACITY : notRacing ? NOT_RACING_OPACITY : 1,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
                  <span style={{ fontSize: 15, lineHeight: 1, flex: "none" }}>{g.icon || "🏆"}</span>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: C.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{g.name || "Untitled"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 10, color: C.muted2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {[g.race_date, g.type].filter(Boolean).join(" · ")}
                  </span>
                  {notRacing ? (
                    <span style={{ fontSize: 10, fontWeight: 600, color: C.muted, flex: "none" }}>not racing</span>
                  ) : (
                    countdown != null && <span style={{ fontSize: 10, fontWeight: 600, color: past ? C.muted : C.fat, flex: "none" }}>{countdown}</span>
                  )}
                </div>
              </div>
            );
          })}
          <div
            onClick={() => setOpen(primary?.event_id ?? null)}
            title="Edit events"
            style={{ cursor: "pointer", flex: "none", display: "flex", alignItems: "center", justifyContent: "center", minWidth: 84, padding: "0 14px", color: C.form, fontSize: 12, fontWeight: 600, background: "transparent", border: `1px dashed ${C.cardBorder}`, borderRadius: 5 }}
          >
            Edit
          </div>
        </div>
      </div>
    </div>
  );
}
