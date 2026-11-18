import { useMemo } from "react";
import { C, DISCIPLINE_COLOR } from "../theme";
import { Card, CardHead, Empty } from "./primitives";
import { groupWorkoutsByDate, localISO } from "../derive";
import { monthDay } from "../format";
import { WEEKDAYS } from "../settings";
import type { RaceEvent, WorkoutRow } from "../types";

// A GitHub-contribution-graph view of the athlete's training: weekdays are
// rows, weeks are columns that start on the day chosen in Settings -> Display
// ("Week starts on"), one cell per day coloured by discipline.
//
// The window ends TODAY, not on the Sunday before the current training week as
// the design handoff specced -- a session synced this morning has to show up
// here. So the LAST column is the partial one, and the days after today render
// transparent, exactly like the lead-in cells before the window start: both are
// days outside the window, and painting them as rest days would claim the
// athlete rested.
const DAYS = 60;
const DAY_MS = 86400000;

// Painting order inside a split cell, so a bike+run brick always reads the same
// way regardless of which leg was stored first.
const ORDER = ["swim", "bike", "run", "strength"];

const addDays = (b: Date, n: number) => new Date(b.getFullYear(), b.getMonth(), b.getDate() + n);
// Days since the start of d's week, which begins on weekday `weekStart`
// (0 = Sunday, 1 = Monday, as getDay()).
const sinceWeekStart = (d: Date, weekStart: 0 | 1) => (d.getDay() - weekStart + 7) % 7;

const CELL_BORDER = "rgba(0,0,0,.22)";

// A race day inside the window gets a gold ring, a glow that bleeds into the
// 5px gap, and a fixed glint across the cell. Its discipline fill is
// deliberately left alone -- the day still has to read as swim/bike/run like
// every other cell; only the frame says "race". Same yellow as the Predicted
// Week's race tile. The shine is static: nothing else on the dashboard moves.
const RACE_BORDER = C.yellow;
const RACE_GLOW =
  "0 0 0 1px rgba(250,222,42,.45), 0 0 11px rgba(250,222,42,.45), inset 0 1px 0 rgba(255,255,255,.5)";
// Diagonal glint laid over the discipline colours, plus a soft gloss down the
// cell -- the two together read as a polished surface rather than a repaint.
const RACE_SHEEN =
  "linear-gradient(118deg, rgba(255,255,255,0) 26%, rgba(255,255,255,.62) 44%, rgba(255,255,255,.14) 52%, rgba(255,255,255,0) 66%), " +
  "linear-gradient(180deg, rgba(255,255,255,.28), rgba(255,255,255,0) 55%, rgba(0,0,0,.18))";

// One cell per day: a solid discipline colour, or an even N-way diagonal split
// on a day that mixed disciplines. Every day is painted at FULL brightness --
// the handoff shaded cells by the day's TSS, but the grid reads as which
// disciplines were trained, not how hard, and the day's load is in the tooltip.
function cellBackground(disciplines: string[]): string {
  const cols = disciplines.map((d) => DISCIPLINE_COLOR[d] ?? DISCIPLINE_COLOR.other);
  if (cols.length === 1) return cols[0];
  const step = 100 / cols.length;
  const stops = cols.map((c, i) => `${c} ${(i * step).toFixed(2)}% ${((i + 1) * step).toFixed(2)}%`);
  return `linear-gradient(115deg, ${stops.join(", ")})`;
}

interface Cell {
  iso: string;
  bg: string;
  border: string;
  tip: string;
  // Name of the race held that day, "" on an ordinary day.
  race: string;
}

function buildGrid(workouts: WorkoutRow[], events: RaceEvent[], weekStart: 0 | 1) {
  const now = new Date();
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const rangeStart = addDays(end, -(DAYS - 1));
  const gridStart = addDays(rangeStart, -sinceWeekStart(rangeStart, weekStart)); // week start on or before
  const gridEnd = addDays(end, 6 - sinceWeekStart(end, weekStart)); // week end on or after
  // Round the day count before dividing: a DST boundary inside the window makes
  // the raw millisecond span an hour off a whole number of days.
  const weeks = (Math.round((gridEnd.getTime() - gridStart.getTime()) / DAY_MS) + 1) / 7;

  const byDate = groupWorkoutsByDate(workouts);
  // Race days come from the events, not from the workouts: nothing on a synced
  // activity says it was a race. Two events on one date keep the first, as in
  // the Predicted Week panel. Future races simply never match a cell.
  const races = new Map<string, RaceEvent>();
  for (const g of events) if (g.race_date && !races.has(g.race_date)) races.set(g.race_date, g);
  const startISO = localISO(rangeStart);
  const endISO = localISO(end);
  let totalTss = 0;
  let hasWorkouts = false;

  // Row di is the di-th day of the week; its name follows the week start.
  const rows = Array.from({ length: 7 }, (_, di) => {
    const cells: Cell[] = Array.from({ length: weeks }, (_, wi) => {
      const d = addDays(gridStart, wi * 7 + di);
      const iso = localISO(d);
      // ISO dates compare correctly as strings.
      if (iso < startISO || iso > endISO) {
        // Outside the window: fully transparent, and no tooltip. The border stays
        // in the box model so every column keeps the same geometry.
        return { iso, bg: "transparent", border: "transparent", tip: "", race: "" };
      }
      const race = races.get(iso);
      const raceName = race?.name || (race ? "Race day" : "");
      const when = race ? `${monthDay(d)} · ${race.name || "race day"}` : monthDay(d);
      const done = byDate.get(iso) ?? [];
      const tss = done.reduce((s, w) => s + (w.tss || 0), 0);
      const present = done.map((w) => w.discipline || "other");
      const disciplines = ORDER.filter((k) => present.includes(k));
      // Anything Garmin reports outside the four known disciplines still gets a
      // slice rather than vanishing from the day.
      for (const k of present) if (!disciplines.includes(k)) disciplines.push(k);

      if (!disciplines.length) {
        // A race with nothing synced against it still gets the ring; the rest
        // fill stays, because there is no discipline to colour it with.
        return {
          iso,
          bg: C.restCell,
          border: race ? RACE_BORDER : C.restCellBorder,
          tip: race ? `${when} · nothing logged` : `${when} · rest day`,
          race: raceName,
        };
      }
      totalTss += tss;
      hasWorkouts = true;
      return {
        iso,
        bg: cellBackground(disciplines),
        border: race ? RACE_BORDER : CELL_BORDER,
        tip: `${when} · ${disciplines.join(" + ")} · ${Math.round(tss)} TSS`,
        race: raceName,
      };
    });
    return { label: WEEKDAYS[(weekStart + di) % 7].short, cells };
  });

  // A month label sits above a column only when that column's month differs
  // from the previous one's, so the row reads "Jul   Aug   Sep".
  let lastMonth = -1;
  const monthLabels = Array.from({ length: weeks }, (_, wi) => {
    const d = addDays(gridStart, wi * 7);
    if (d.getMonth() === lastMonth) return "";
    lastMonth = d.getMonth();
    return d.toLocaleDateString(undefined, { month: "short" });
  });

  return { rows, monthLabels, rangeStart, end, totalTss: Math.round(totalTss), hasWorkouts };
}

export function TrainingDays({
  workouts,
  events,
  weekStart,
}: {
  workouts: WorkoutRow[];
  events: RaceEvent[];
  weekStart: 0 | 1;
}) {
  const grid = useMemo(() => buildGrid(workouts, events, weekStart), [workouts, events, weekStart]);
  const title = (
    <>
      Training Days <span style={{ color: C.muted, fontWeight: 400 }}>· last 60 days</span>
    </>
  );

  if (!grid.hasWorkouts) {
    return (
      <Card>
        <CardHead title={title} right="awaiting synced workouts" />
        <Empty label="No synced workouts in the last 60 days — run a Garmin sync." />
      </Card>
    );
  }

  return (
    <Card>
      <CardHead
        title={title}
        right={`${monthDay(grid.rangeStart)} → ${monthDay(grid.end)} · ${grid.totalTss} TSS`}
      />
      <div style={{ padding: "14px 13px 14px", display: "flex", flexDirection: "column", gap: 5 }}>
        <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
          <span style={{ width: 32, flex: "none" }} />
          {grid.monthLabels.map((m, i) => (
            <span key={i} style={{ flex: 1, minWidth: 0, fontSize: 10, color: C.muted, textAlign: "left" }}>
              {m}
            </span>
          ))}
        </div>
        {grid.rows.map((row, ri) => (
          <div key={ri} style={{ display: "flex", gap: 5, alignItems: "center" }}>
            <span style={{ width: 32, flex: "none", fontSize: 10, color: C.muted, textAlign: "right" }}>
              {row.label}
            </span>
            {row.cells.map((c) => (
              <div
                key={c.iso}
                title={c.tip}
                style={{
                  position: "relative",
                  overflow: "hidden",
                  flex: 1,
                  minWidth: 0,
                  height: 15,
                  borderRadius: 6,
                  background: c.bg,
                  border: `1px solid ${c.border}`,
                  boxShadow: c.race ? RACE_GLOW : undefined,
                  // Lift the race cell so its glow lands on top of the two
                  // neighbours it bleeds into, not under them.
                  zIndex: c.race ? 1 : undefined,
                }}
              >
                {c.race ? (
                  <span
                    style={{ position: "absolute", inset: 0, background: RACE_SHEEN, pointerEvents: "none" }}
                  />
                ) : null}
              </div>
            ))}
          </div>
        ))}
      </div>
    </Card>
  );
}
