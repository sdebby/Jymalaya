import { useEffect, useState } from "react";
import { C, DISCIPLINE_COLOR } from "../theme";
import { km, monthDay, pace, speedKmh } from "../format";
import { useModalKeys } from "../useModalKeys";
import { api } from "../api";
import { Spinner } from "./TopBar";
import { ChatIcon } from "./ChatIcon";
import {
  BodyText,
  Chip,
  ChipRow,
  ModalBody,
  ModalHeader,
  ModalScrim,
  ModalShell,
  Section,
  Tag,
  TagGrid,
  cap,
  withDividers,
} from "./SessionModal";
import { WorkoutStreamGraphs, ZoneBar } from "../charts/WorkoutStreams";
import { TrainingEffectSection } from "../charts/TrainingEffect";
import { WorkoutRating } from "../charts/WorkoutRating";
import { LoadImpactSection } from "../charts/LoadImpact";
import type { WorkoutAnalysis, WorkoutRow, WorkoutStreams } from "../types";

// Detail view for a completed (synced) workout — the past-panel counterpart of
// SessionModal. Same layout, but real Garmin metrics, plus the on-demand coach
// analysis (PRD section 7.9): the footer's Analyze button pulls this activity's
// detailed Garmin data and has Claude review it, and the insights render inline.
export function WorkoutModal({
  workout,
  date,
  dayName,
  coachRecommendation,
  onAskCoach,
  onClose,
}: {
  workout: WorkoutRow;
  date: string;
  dayName: string;
  // Set when this workout happened on a day the plan marked as rest — explains
  // why the plan and the synced activity disagree.
  coachRecommendation?: string;
  // Attach this workout to the coach chat. The backend sends its full recorded
  // data, and the cached analysis when there is one.
  onAskCoach?: () => void;
  onClose: () => void;
}) {
  const accent = DISCIPLINE_COLOR[workout.discipline] ?? C.muted;
  const zones = parseZones(workout.zones_time_sec);

  const [analysis, setAnalysis] = useState<WorkoutAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The recorded streams for the graphs. undefined while loading, null when
  // nothing is stored -- then the modal falls back to the metric tiles.
  const [streams, setStreams] = useState<WorkoutStreams | null | undefined>(undefined);

  // View-only modal: Esc closes, no confirm action.
  useModalKeys(onClose);

  // A previously analyzed workout shows its insights immediately — the cached
  // file is served without touching Garmin or Claude.
  useEffect(() => {
    let live = true;
    api.workoutAnalysis(workout.activity_id).then((a) => {
      if (live) setAnalysis(a);
    });
    return () => {
      live = false;
    };
  }, [workout.activity_id]);

  useEffect(() => {
    const ctl = new AbortController();
    setStreams(undefined);
    api.workoutStreams(workout.activity_id, ctl.signal).then(setStreams, () => {});
    return () => ctl.abort();
  }, [workout.activity_id]);

  async function runAnalysis(force: boolean) {
    setAnalyzing(true);
    setError(null);
    try {
      setAnalysis(await api.analyzeWorkout(workout.activity_id, force));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalyzing(false);
    }
  }

  // Cadence unit follows the discipline of the recording device.
  const cadenceUnit = workout.discipline === "bike" ? "rpm" : workout.discipline === "swim" ? "str/min" : "spm";

  const metrics: { label: string; value: string }[] = [];
  if (workout.avg_hr != null) metrics.push({ label: "Avg HR", value: `${Math.round(workout.avg_hr)} bpm` });
  if (workout.avg_power != null) metrics.push({ label: "Avg power", value: `${Math.round(workout.avg_power)} W` });
  if (workout.max_power != null) metrics.push({ label: "Max power", value: `${Math.round(workout.max_power)} W` });
  if (workout.normalized_power != null) metrics.push({ label: "Norm. power", value: `${Math.round(workout.normalized_power)} W` });
  if (workout.avg_cadence != null) metrics.push({ label: "Avg cadence", value: `${Math.round(workout.avg_cadence)} ${cadenceUnit}` });
  if (workout.avg_pace_sec_per_km != null)
    metrics.push(
      workout.discipline === "bike"
        ? { label: "Avg speed", value: `${speedKmh(workout.avg_pace_sec_per_km)} km/h` }
        : { label: "Avg pace", value: `${pace(workout.avg_pace_sec_per_km)} /km` },
    );
  if (workout.avg_pace_sec_per_100m != null) metrics.push({ label: "Avg pace", value: `${pace(workout.avg_pace_sec_per_100m)} /100m` });
  if (workout.intensity_factor != null) metrics.push({ label: "IF", value: workout.intensity_factor.toFixed(2) });
  if (workout.elevation_gain_m != null) metrics.push({ label: "Elev. gain", value: `${Math.round(workout.elevation_gain_m)} m` });
  if (workout.elevation_loss_m != null) metrics.push({ label: "Elev. loss", value: `${Math.round(workout.elevation_loss_m)} m` });
  if (workout.calories != null) metrics.push({ label: "Calories", value: `${Math.round(workout.calories)} kcal` });

  // With the streams loaded, the graphs carry what the metric tiles used to
  // (each graph's head shows its average and maximum), and IF and calories
  // join the top row. Without them the old tiles stay.
  const graphs = streams && streams.samples.length > 1;
  const sections: React.ReactNode[] = [
    <TagGrid key="stats" columns={graphs ? 6 : 4}>
      <Tag label="Discipline" value={cap(workout.discipline)} color={accent} />
      <Tag label="Duration" value={workout.duration_sec != null ? `${Math.round(workout.duration_sec / 60)} min` : "—"} />
      <Tag
        label="Distance"
        value={
          workout.distance_m == null
            ? "—"
            : workout.discipline === "swim"
              ? `${Math.round(workout.distance_m)} m`
              : `${km(workout.distance_m)} km`
        }
      />
      <Tag label="TSS" value={workout.tss != null ? String(Math.round(workout.tss)) : "—"} color={C.yellow} />
      {graphs && <Tag label="IF" value={workout.intensity_factor != null ? workout.intensity_factor.toFixed(2) : "—"} />}
      {graphs && <Tag label="Calories" value={workout.calories != null ? `${Math.round(workout.calories)} kcal` : "—"} />}
    </TagGrid>,
  ];
  if (graphs) {
    sections.push(
      <Section key="graphs" title="Workout graphs">
        <span style={{ marginTop: -6, fontSize: 12, color: C.mutedBright }}>
          every {streams.bucket_sec} s · hover to read all at one moment
        </span>
        <WorkoutStreamGraphs discipline={workout.discipline} streams={streams} workout={workout} />
      </Section>,
    );
  } else if (streams === undefined) {
    sections.push(<BodyText key="loading">Loading workout graphs…</BodyText>);
  } else if (metrics.length > 0) {
    sections.push(
      <Section key="metrics" title="Recorded metrics">
        <TagGrid>
          {metrics.map((m) => (
            <Tag key={m.label} label={m.label} value={m.value} />
          ))}
        </TagGrid>
      </Section>,
    );
  }
  const powerZones = streams?.power_zones;
  if (zones.length > 0 || powerZones) {
    // Time below the Z1 floor belongs to no zone (coasting, most of a pool swim).
    const zoneSec = zones.reduce((total, [, sec]) => total + sec, 0);
    const belowSec = workout.duration_sec != null ? workout.duration_sec - zoneSec : 0;
    sections.push(
      <Section key="zones" title="Time in zones">
        {zones.length > 0 && (
          <ZoneBar title="Heart rate" basis="% LTHR bands" seconds={Object.fromEntries(zones)} belowSec={belowSec} />
        )}
        {powerZones && <ZoneBar title="Power" basis="% FTP bands" seconds={powerZones} />}
      </Section>,
    );
  }
  // Garmin's Training Effect, below Time in zones. Absent on workouts synced
  // before it was stored and on activities Garmin scores no effect for.
  const hasTe = workout.aerobic_te != null || workout.anaerobic_te != null;
  // The athlete's own RPE and feel sit right under the dials, in the same
  // section (no divider between them); alone when Garmin scored no effect.
  const hasRating = workout.rpe != null || workout.feel != null;
  // What the workout did to Fitness, Fatigue and Form, directly above the dials
  // in the same section.
  const hasImpact = workout.tss != null && workout.tss > 0;
  if (hasTe || hasRating || hasImpact) {
    sections.push(
      <div key="training-effect" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {hasImpact && (
          <div style={{ marginBottom: hasTe || hasRating ? 12 : 0 }}>
            <LoadImpactSection tss={workout.tss!} />
          </div>
        )}
        {hasTe && (
          <TrainingEffectSection aerobic={workout.aerobic_te} anaerobic={workout.anaerobic_te} label={workout.te_label} />
        )}
        {hasRating && <WorkoutRating rpe={workout.rpe} feel={workout.feel} />}
      </div>,
    );
  }
  if (coachRecommendation) {
    sections.push(
      <Section key="recommendation" title="Coach recommendation">
        <BodyText>{coachRecommendation}</BodyText>
      </Section>,
    );
  }
  if (error) {
    sections.push(
      <div
        key="error"
        style={{ background: "#2a1416", border: `1px solid ${C.red}`, borderRadius: 8, padding: "10px 12px", fontSize: 12.5, color: C.text }}
      >
        Analysis failed: {error}
      </div>,
    );
  }
  if (analysis) sections.push(...analysisSections(analysis));
  if (metrics.length === 0 && zones.length === 0 && !hasTe && !hasRating && !hasImpact && !coachRecommendation && !analysis && !error) {
    sections.push(<BodyText key="empty">No further detail for this workout.</BodyText>);
  }

  return (
    <ModalScrim onClose={onClose}>
      <ModalShell width="70vw">
        <ModalHeader
          accent={accent}
          title={workout.title || `${cap(workout.discipline)} workout`}
          subtitle={
            <>
              {dayName} · {monthDay(date)} ·{" "}
              <span style={{ color: C.successBright, fontWeight: 600 }}>completed</span>
            </>
          }
          onClose={onClose}
        />
        <ModalBody gap={24}>{withDividers(sections)}</ModalBody>

        <div style={{ display: "flex", justifyContent: "center", gap: 10, padding: "16px 26px", borderTop: `1px solid ${C.modalBorder}`, background: C.bar, flex: "none" }}>
          <button
            onClick={analyzing ? undefined : () => runAnalysis(analysis != null)}
            title={analysis ? "Run the analysis again" : "Analyze this workout with the AI coach"}
            style={{
              cursor: analyzing ? "default" : "pointer",
              display: "flex",
              alignItems: "center",
              gap: 8,
              background: C.purple,
              opacity: analyzing ? 0.7 : 1,
              border: "none",
              borderRadius: 8,
              padding: "11px 20px",
              fontSize: 14,
              fontWeight: 600,
              color: "#fff",
            }}
          >
            {analyzing && <Spinner />}
            {analyzing ? "Analyzing…" : analysis ? "Re-analyze" : "Analyze workout"}
          </button>
          {onAskCoach && (
            <button
              onClick={() => {
                onAskCoach();
                onClose();
              }}
              title="Ask coach"
              aria-label="Ask coach"
              style={{ cursor: "pointer", background: C.form, border: "none", borderRadius: 8, width: 44, height: 44, padding: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#fff" }}
            >
              <ChatIcon />
            </button>
          )}
        </div>
      </ModalShell>
    </ModalScrim>
  );
}

// The analysis rendered as ordinary modal sections. Every field is optional —
// the model may omit any of them — so each block is emitted only when present.
// No headline, summary paragraph or metric notes (dropped 2026-11-07, the
// athlete's call): a cached analysis that still carries them shows without them.
function analysisSections(a: WorkoutAnalysis): React.ReactNode[] {
  const out: React.ReactNode[] = [];

  const adherence = a.plan_adherence;
  if (adherence?.verdict || adherence?.detail) {
    out.push(
      <Section key="a-adherence" title="Execution vs plan">
        {adherence.verdict && (
          <ChipRow columns={1}>
            <Chip name={adherence.verdict} detail={adherence.had_structured_workout ? "structured workout" : "no structured workout"} />
          </ChipRow>
        )}
        {adherence.detail && <BodyText>{adherence.detail}</BodyText>}
      </Section>,
    );
  }

  for (const [key, title, items] of [
    ["well", "What went well", a.what_went_well],
    ["improve", "What to improve", a.what_to_improve],
    ["flags", "Flags", a.flags],
    ["form", "Against your current form", formPoints(a.form_context)],
  ] as const) {
    const list = items?.filter(Boolean) ?? [];
    if (list.length === 0) continue;
    out.push(
      <Section key={`a-${key}`} title={title}>
        <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
          {list.map((item, i) => (
            <li key={i} style={{ fontSize: 13, lineHeight: 1.55, color: key === "flags" ? C.dangerText : C.mutedBright2 }}>
              {item}
            </li>
          ))}
        </ul>
      </Section>,
    );
  }

  return out;
}

// form_context is a list of points; an analysis cached before that holds one
// paragraph, which is split into its sentences so it reads as bullets too.
function formPoints(raw?: string[] | string): string[] {
  if (Array.isArray(raw)) return raw;
  if (!raw) return [];
  return raw.split(/(?<=[.!?])\s+(?=[A-Z])/).map((t) => t.trim()).filter(Boolean);
}

// Chips are labelled in whole minutes, so a zone the athlete only clipped
// through renders as "0 min" and takes a column from the ones that matter.
const MIN_ZONE_SEC = 30;

// zones_time_sec is stored as a JSON string ({"Z1": seconds, ...}); zones with
// no meaningful time are dropped and the rest sorted Z1..Z5.
function parseZones(raw?: string): [string, number][] {
  if (!raw) return [];
  try {
    const obj = JSON.parse(raw) as Record<string, number>;
    return Object.entries(obj)
      .filter(([, sec]) => typeof sec === "number" && sec >= MIN_ZONE_SEC)
      .sort(([a], [b]) => a.localeCompare(b));
  } catch {
    return [];
  }
}
