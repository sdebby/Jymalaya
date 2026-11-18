// The five Form (TSB) zones, as drawn behind the Fatigue Zones chart. Names,
// boundaries and meanings follow Joe Friel, "Managing Training Using TSB"
// (2015): High Risk below -30, Optimal Training -30 to -10, Grey Zone -10 to
// +5, Freshness +5 to +25, Transitional above +25. Friel notes the ranges suit
// most athletes but not all, and Science to Sport ("Monitoring Training Load")
// adds that the race-day TSB an athlete performs best at is individual -- some
// race best at +10 to +20, others at -5 to +5. An open end (Infinity) runs to
// the edge of the plot.
//
// Colours are the dashboard's existing accents, so each carries the meaning it
// already has here: Freshness is Form blue, Optimal Training is Fitness green,
// High Risk is red. Transitional is yellow (Friel's chart uses orange, which
// here means bike), the one bright accent with no discipline meaning.
import { C } from "./theme";

export interface FormZone {
  name: string;
  // Short label drawn inside the band on the chart.
  short: string;
  // What the zone means for training, in a few words, after Friel.
  advice: string;
  from: number;
  to: number;
  color: string;
}

// Highest first, the order they stack on the chart.
export const FORM_ZONES: FormZone[] = [
  { name: "Transitional", short: "Transitional", advice: "little training, fitness falling", from: 25, to: Infinity, color: C.yellow },
  { name: "Freshness", short: "Fresh", advice: "race-ready, or an end-of-season break", from: 5, to: 25, color: C.form },
  { name: "Grey Zone", short: "Grey", advice: "rest week or taper, little fitness gain", from: -10, to: 5, color: C.muted },
  { name: "Optimal Training", short: "Optimal", advice: "most effective training", from: -30, to: -10, color: C.fit },
  { name: "High Risk", short: "High Risk", advice: "overreaching, ease off within a few days", from: -Infinity, to: -30, color: C.red },
];

// Gridlines sit on the zone boundaries.
export const FORM_ZONE_TICKS = [25, 5, -10, -30];

// A value on a boundary belongs to the zone above it, except -30 and -10,
// which stay in Optimal Training and Grey Zone (the lower bound is inclusive
// there).
export function formZoneOf(tsb: number): FormZone {
  if (tsb > 25) return FORM_ZONES[0];
  if (tsb > 5) return FORM_ZONES[1];
  if (tsb >= -10) return FORM_ZONES[2];
  if (tsb >= -30) return FORM_ZONES[3];
  return FORM_ZONES[4];
}

// Consecutive days, ending with the last value, spent in that value's zone.
// Friel's rule for High Risk is a few days at a time, so the run length is the
// number that matters, not the total.
export function formZoneStreak(tsb: number[]): number {
  if (!tsb.length) return 0;
  const zone = formZoneOf(tsb[tsb.length - 1]);
  let n = 0;
  for (let i = tsb.length - 1; i >= 0 && formZoneOf(tsb[i]) === zone; i--) n++;
  return n;
}

// Y range that always shows every zone boundary plus enough of the outer zones
// to label them, and stretches if the athlete's form ever leaves that window.
export function formZoneDomain(tsb: number[]): { min: number; max: number } {
  const lo = Math.min(...tsb);
  const hi = Math.max(...tsb);
  return { min: Math.min(-45, Math.floor(lo - 3)), max: Math.max(35, Math.ceil(hi + 3)) };
}

export const signedInt = (v: number) => (Math.round(v) > 0 ? "+" : "") + Math.round(v);
