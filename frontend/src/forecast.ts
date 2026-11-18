// Project CTL/ATL/TSB across the next-7-days plan (pure math, no React).
// Mirrors the backend recurrence in app/metrics/calculator.py (_step), the
// linear EWMA of assets/Jymalaya_AI-rest_day_TSS.md:
//   L_next = L_prev + (TSS_day - L_prev) / tau
// with CTL tau=42, ATL tau=7. TSB for a day is yesterday's CTL - ATL.
// Keep this in step with the backend: they must not drift apart.

import type { Plan } from "./types";

const CTL_TAU = 42;
const ATL_TAU = 7;
const DAY = 86400000;

export interface ForecastDay {
  label: string; // 3-letter day name
  date: string;
  tss: number; // planned daily TSS (0 on days with no planned session)
}

export interface Forecast {
  days: ForecastDay[];
  ctl: number[]; // projected CTL at the end of each plan day
  tsb: number[]; // projected TSB entering each plan day
  end: { ctl: number; tsb: number; deltaCtl: number };
}

function step(ctl: number, atl: number, tss: number): [number, number] {
  return [ctl + (tss - ctl) / CTL_TAU, atl + (tss - atl) / ATL_TAU];
}

export function buildForecast(
  plan: Plan | null,
  current: { ctl: number; atl: number },
): Forecast | null {
  if (!plan || !plan.days?.length) return null;

  let ctl = current.ctl;
  let atl = current.atl;
  const startCtl = ctl;

  // Decay across the gap days between today and the plan's first day (0 TSS).
  const first = plan.days[0]?.date;
  if (first) {
    const gap = Math.round((new Date(first).getTime() - Date.now()) / DAY);
    for (let i = 0; i < Math.min(Math.max(gap, 0), 14); i++) [ctl, atl] = step(ctl, atl, 0);
  }

  const days: ForecastDay[] = [];
  const ctlSeries: number[] = [];
  const tsbSeries: number[] = [];
  for (const d of plan.days) {
    // The day's sessions are the only source of load, same rule as the
    // predicted-week card: a session the coach added to a schedule rest day
    // is real load and has to show up in the forecast.
    const tss = (d.sessions ?? []).reduce((s, x) => s + (x.estimated_tss || 0), 0);
    tsbSeries.push(ctl - atl); // form entering the day (yesterday's values)
    [ctl, atl] = step(ctl, atl, tss);
    days.push({ label: String(d.day_name).slice(0, 3), date: d.date, tss });
    ctlSeries.push(ctl);
  }

  return {
    days,
    ctl: ctlSeries,
    tsb: tsbSeries,
    end: { ctl, tsb: ctl - atl, deltaCtl: ctl - startCtl },
  };
}
