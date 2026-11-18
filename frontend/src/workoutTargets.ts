// A plan segment's intensity target, for the session modal's workout graph.
//
// Mirrors the bands Send to device puts on the watch (app/intervals/workout.py:
// RUN_HR_BANDS, BIKE_POWER_BANDS, SWIM_PACE_BANDS, step_target), so the graph
// shows exactly the step the watch will get. Keep the two in step.

import type { AthleteProfile, StructureSegment } from "./types";

type Band = [number, number];

// % of LTHR. Z5 closed at 106, as on the watch.
const RUN_HR_BANDS: Record<string, Band> = { Z1: [65, 80], Z2: [80, 89], Z3: [89, 96], Z4: [96, 100], Z5: [100, 106] };

// % of FTP. Z1 floored at 45: a 0 W target is no target at all.
const BIKE_FLOOR_PCT = 45;
const BIKE_POWER_BANDS: Record<string, Band> = { Z1: [BIKE_FLOOR_PCT, 55], Z2: [56, 75], Z3: [76, 90], Z4: [91, 105], Z5: [106, 120] };

// css_sec_100m multiplied by these, faster end first.
const SWIM_PACE_BANDS: Record<string, Band> = { Z1: [1.21, 1.35], Z2: [1.1, 1.21], Z3: [1.03, 1.1], Z4: [0.98, 1.03], Z5: [0.9, 0.98] };

// The graph's y-axis: what 100% means for each discipline.
export const THRESHOLD_LABEL: Record<string, string> = { run: "LTHR", bike: "FTP", swim: "CSS" };

export interface SegmentTarget {
  // Intensity as % of threshold (swim: % of CSS speed), low and high end.
  // Null for a step with no target (strength, or no zone).
  pct: Band | null;
  // The zone the colour is taken from ("Z2"), when one applies.
  zone: string | null;
  // The target in the units the athlete reads: "108-148 bpm (65-89%)",
  // "160-214 W (56-75%)", "2:31-2:45 /100m". Empty when there is none.
  text: string;
}

const ZONE_RE = /^\s*z([1-5])\s*(?:[-–—]\s*z?([1-5]))?\s*$/i;
const WATTS_RE = /^\s*(\d+(?:\.\d+)?)\s*w?\s*(?:[-–—]\s*(\d+(?:\.\d+)?)\s*w?)?\s*$/i;

// "Z4" -> ["Z4"]; "Z1-Z2" -> ["Z1", "Z2"]; anything else -> null (as _zones).
function zonesOf(label: string | undefined): string[] | null {
  const m = ZONE_RE.exec(label ?? "");
  if (!m) return null;
  const a = Number(m[1]);
  const b = Number(m[2] ?? m[1]);
  const out: string[] = [];
  for (let n = Math.min(a, b); n <= Math.max(a, b); n++) out.push(`Z${n}`);
  return out;
}

function band(bands: Record<string, Band>, zones: string[]): Band {
  return [Math.min(...zones.map((z) => bands[z][0])), Math.max(...zones.map((z) => bands[z][1]))];
}

function positive(v: unknown): number | null {
  return typeof v === "number" && v > 0 ? v : null;
}

function mmss(sec: number): string {
  const t = Math.round(sec);
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

const range = (lo: number, hi: number) => (lo === hi ? `${lo}` : `${lo}-${hi}`);

// The bike zone a % of FTP falls in, for colouring a watts-only target.
function bikeZoneAt(pct: number): string {
  for (const z of ["Z5", "Z4", "Z3", "Z2"]) if (pct >= BIKE_POWER_BANDS[z][0]) return z;
  return "Z1";
}

export function segmentTarget(discipline: string, seg: StructureSegment, profile: AthleteProfile | null): SegmentTarget {
  const zones = zonesOf(seg.zone);
  const none: SegmentTarget = { pct: null, zone: null, text: "" };

  if (discipline === "run") {
    if (!zones) return none;
    const [lo, hi] = band(RUN_HR_BANDS, zones);
    const lthr = positive(profile?.lthr_bpm);
    const text = lthr
      ? `${range(Math.round((lthr * lo) / 100), Math.round((lthr * hi) / 100))} bpm (${range(lo, hi)}%)`
      : `${range(lo, hi)}% LTHR`;
    return { pct: [lo, hi], zone: zones[0], text };
  }

  if (discipline === "bike") {
    const ftp = positive(profile?.ftp_w);
    // The watts Claude wrote win over the zone: they are what the modal lists.
    const m = WATTS_RE.exec(seg.target_power_w ?? "");
    let watts: Band | null = null;
    if (m) {
      let lo = Number(m[1]);
      let hi = Number(m[2] ?? m[1]);
      [lo, hi] = [Math.min(lo, hi), Math.max(lo, hi)];
      if (ftp && lo < (ftp * BIKE_FLOOR_PCT) / 100) lo = Math.min((ftp * BIKE_FLOOR_PCT) / 100, hi);
      watts = [Math.round(lo), Math.round(hi)];
    } else if (zones && ftp) {
      const [lo, hi] = band(BIKE_POWER_BANDS, zones);
      watts = [Math.round((ftp * lo) / 100), Math.round((ftp * hi) / 100)];
    }
    if (watts && watts[1] > 0) {
      if (!ftp) return { pct: zones ? band(BIKE_POWER_BANDS, zones) : null, zone: zones?.[0] ?? null, text: `${range(...watts)} W` };
      const pct: Band = [Math.round((watts[0] / ftp) * 100), Math.round((watts[1] / ftp) * 100)];
      return { pct, zone: zones?.[0] ?? bikeZoneAt((pct[0] + pct[1]) / 2), text: `${range(...watts)} W (${range(...pct)}%)` };
    }
    if (!zones) return none;
    const pct = band(BIKE_POWER_BANDS, zones);
    return { pct, zone: zones[0], text: `${range(...pct)}% FTP` };
  }

  if (discipline === "swim") {
    if (!zones) return none;
    const [fast, slow] = band(SWIM_PACE_BANDS, zones);
    // Higher on the graph is faster: intensity is speed as a % of CSS speed.
    const pct: Band = [Math.round(100 / slow), Math.round(100 / fast)];
    const css = positive(profile?.css_sec_100m);
    const text = css ? `${mmss(css * fast)}-${mmss(css * slow)} /100m` : `${range(...pct)}% CSS`;
    return { pct, zone: zones[0], text };
  }

  return none; // strength: time only
}

// Minutes as the watch writes a step length: 1h30m, 12m, 2m30s.
export function stepDuration(min: number): string {
  const s = Math.round(min * 60);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return [h && `${h}h`, m && `${m}m`, sec && `${sec}s`].filter(Boolean).join("") || "0m";
}
