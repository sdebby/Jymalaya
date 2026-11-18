"""
Training-load metrics: TSS per discipline and CTL/ATL/TSB.

Bike and run follow assets/PRD.md section 6, whose formulas reduce to one form:

    TSS = (duration_sec / 3600) * IF**2 * 100

because cTSS's `(NP * IF) / FTP` term equals IF**2 once IF = NP/FTP, and the run
formula is the same shape. Swim is a conscious divergence from PRD section 6
(which squares IF): TrainingPeaks' sTSS cubes it, because power against water
drag scales with velocity cubed, and matching TrainingPeaks is what the athlete
expects. So per-discipline:

    bike : IF = NP / FTP                       (NP, else avg power as a proxy)   IF**2
    run  : IF = threshold_pace / NGP           (sec/km; faster -> IF > 1)        IF**2
                (average pace when the run has no stored FIT samples)
    swim : IF = CSS_pace / moving_pace         (sec/100m over MOVING time,       IF**3
                                                rest between sets excluded)
    fallback (any): IF = avg_HR / LTHR         (HR-based, when no power/pace)    IF**2

Swim follows assets/tss_rtss_calculation_spec.md section 4: both the pace and
the hours charged come from moving time, so sTSS = IF**3 * moving_hours * 100.
The spec writes it in m/min (NSS / swim FTP); CSS_pace / moving_pace in sec/100m
is the same ratio, and sec/100m is what the athlete profile stores.

Degrade gracefully: when the inputs for a workout aren't available, estimate_tss
returns (None, None) instead of raising, and the caller records a gap.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

CTL_TAU = 42
ATL_TAU = 7


# --- TSS ------------------------------------------------------------------
def swim_moving_seconds(workout: dict) -> float | None:
    """
    The seconds a swim's sTSS is charged over: moving time, rest between sets
    excluded. Falls back to the timer duration for rows the parser could not
    determine moving time for (and for continuous open-water swims, where the
    two are the same anyway).
    """
    return workout.get("moving_duration_sec") or workout.get("duration_sec")


def swim_pace_sec_per_100m(moving_sec, distance_m) -> float | None:
    """Swim pace over moving time -- the spec's NSS, expressed as sec/100m."""
    if not moving_sec or not distance_m:
        return None
    return round(moving_sec / distance_m * 100.0, 1)


def _intensity(workout: dict, profile: dict) -> tuple[float | None, int, float | None, str | None]:
    """
    (IF, exponent, seconds, method) for the TSS formula. Exponent 3 and
    moving-time seconds apply only to pace-based swim; everything else is IF**2
    over the full duration -- including a swim that falls through to the HR
    fallback, since hrTSS is defined over time spent at a heart rate, rest
    included.

    method names the basis, stored as `tss_method`: "power" (bike NP),
    "ngp" (run, Normalized Graded Pace from the FIT samples, app/metrics/ngp.py),
    "pace" (run average pace -- a run with no stored samples), "css" (swim),
    "hr" (the fallback).
    """
    disc = workout.get("discipline")
    duration = workout.get("duration_sec")

    if disc == "bike":
        power = workout.get("normalized_power") or workout.get("avg_power")
        ftp = profile.get("ftp_w")
        if power and ftp:
            return power / ftp, 2, duration, "power"

    elif disc == "run":
        threshold = profile.get("run_threshold_pace_sec_km")
        ngp = workout.get("ngp_sec_per_km")
        if ngp and threshold:
            return threshold / ngp, 2, duration, "ngp"
        actual = workout.get("avg_pace_sec_per_km")
        if actual and threshold:
            return threshold / actual, 2, duration, "pace"

    elif disc == "swim":
        actual = workout.get("avg_pace_sec_per_100m")
        css = profile.get("css_sec_100m")
        if actual and css:
            return css / actual, 3, swim_moving_seconds(workout), "css"

    # HR-based fallback (works for any discipline, incl. strength/other).
    avg_hr = workout.get("avg_hr")
    lthr = profile.get("lthr_bpm")
    if avg_hr and lthr:
        return avg_hr / lthr, 2, duration, "hr"

    return None, 2, duration, None


def estimate_tss_method(workout: dict, profile: dict) -> tuple[float | None, float | None, str | None]:
    """(tss, intensity_factor, method) for one workout, or (None, None, None) on a gap."""
    intensity, exponent, seconds, method = _intensity(workout, profile)
    if intensity is None or not seconds:
        return None, None, None
    tss = (seconds / 3600.0) * intensity**exponent * 100.0
    return round(tss, 1), round(intensity, 3), method


def estimate_tss(workout: dict, profile: dict) -> tuple[float | None, float | None]:
    """Return (tss, intensity_factor) for one workout, or (None, None) on a gap."""
    tss, intensity, _ = estimate_tss_method(workout, profile)
    return tss, intensity


def apply_tss(workouts: list[dict], profile: dict) -> list[dict]:
    """Fill `tss`, `intensity_factor` and `tss_method` in place; return the list."""
    for w in workouts:
        w["tss"], w["intensity_factor"], w["tss_method"] = estimate_tss_method(w, profile)
    return workouts


# --- daily aggregation ----------------------------------------------------
def daily_tss(workouts: list[dict], discipline: str | None = None) -> dict[str, float]:
    """Sum TSS per ISO date, optionally filtered to one discipline."""
    totals: dict[str, float] = defaultdict(float)
    for w in workouts:
        if discipline and w.get("discipline") != discipline:
            continue
        if w.get("tss") and w.get("date"):
            totals[w["date"]] += w["tss"]
    return dict(totals)


# --- CTL / ATL / TSB ------------------------------------------------------
def _step(ctl: float, atl: float, tss: float) -> tuple[float, float]:
    """
    One day of the linear EWMA recurrence (assets/Jymalaya_AI-rest_day_TSS.md):

        L_today = L_yest + (TSS_today - L_yest) / tau

    A conscious divergence from PRD section 6.2, which writes the exponential
    form; the two agree to ~0.03%/day. On a rest day (tss = 0) this reduces to
    pure decay -- CTL loses 1/42 of its value, ATL 1/7 -- which is why fatigue
    falls away faster than fitness and form trends up across a rest streak.
    """
    return ctl + (tss - ctl) / CTL_TAU, atl + (tss - atl) / ATL_TAU


def _load_row(d: date, discipline: str, tss: float, ctl: float, atl: float) -> dict:
    """
    One daily_load row. Values are rounded for storage/display only -- callers
    iterate on the unrounded ctl/atl so a long rest streak cannot accumulate
    rounding error.
    """
    return {
        "date": d.isoformat(),
        "discipline": discipline,
        "tss": round(tss, 1),
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(ctl - atl, 1),
    }


def compute_load(
    daily_tss_by_date: dict[str, float],
    discipline: str = "all",
    seed_ctl: float = 0.0,
    seed_atl: float = 0.0,
    through: date | None = None,
) -> list[dict]:
    """
    Walk a continuous daily series (gaps filled with 0 TSS) applying _step.

    The series runs to `through` (default today), NOT to the last day that has a
    workout: trailing rest days have to decay too. Ending it on the last workout
    freezes ATL at its peak, so fatigue never recovers and form never rises --
    and every consumer reading the last row treats that stale day as "now".
    `through` only ever extends the series; a value before the last workout
    leaves it ending at the workout.

    Returns one row per day: {date, discipline, tss, ctl, atl, tsb}.
    """
    if not daily_tss_by_date:
        return []

    dates = sorted(daily_tss_by_date)
    start = date.fromisoformat(dates[0])
    end = max(date.fromisoformat(dates[-1]), through or date.today())

    ctl, atl = seed_ctl, seed_atl
    rows: list[dict] = []

    d = start
    while d <= end:
        tss = daily_tss_by_date.get(d.isoformat(), 0.0)
        ctl, atl = _step(ctl, atl, tss)
        rows.append(_load_row(d, discipline, tss, ctl, atl))
        d += timedelta(days=1)

    return rows


def _row_date(row: dict) -> date | None:
    """
    A stored daily_load row's calendar day. Rows read back from InfluxDB carry a
    `time` datetime (InfluxRepo.query renames _time); rows straight out of
    compute_load carry a `date` string.
    """
    raw = row.get("date")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    t = row.get("time")
    if isinstance(t, datetime):
        # daily_load points are stamped at midnight UTC (influx.client._parse_date),
        # so the UTC date is the calendar day -- converting to local would shift it.
        return t.astimezone(timezone.utc).date()
    return None


def pad_load_to(rows: list[dict], through: date | None = None) -> list[dict]:
    """
    Extend a stored daily_load series forward to `through` (default today) with
    zero-TSS days, so readers always see load as of today rather than as of the
    last sync. compute_load already writes those days at sync time; this keeps
    the series honest between syncs, using the same recurrence.

    Never pads past today by default: daily_load reads use `range(start: -Nd)`,
    whose implicit stop is now(), so a future-dated row would be invisible.
    Returns the rows unchanged when they already reach `through`.

    Padded days resume from the stored 1-decimal ctl/atl rather than the full
    precision compute_load carried, so they can sit a rounding step (<= 0.05)
    off the sync-time walk. That gap shrinks each day instead of compounding
    -- every step pulls the value toward the same target -- and the next sync
    overwrites these days from the full history anyway.
    """
    if not rows:
        return rows
    last = rows[-1]
    last_day = _row_date(last)
    end = through or date.today()
    if last_day is None or last_day >= end:
        return rows

    discipline = last.get("discipline") or "all"
    ctl = float(last.get("ctl") or 0.0)
    atl = float(last.get("atl") or 0.0)
    out = list(rows)
    d = last_day + timedelta(days=1)
    while d <= end:
        ctl, atl = _step(ctl, atl, 0.0)
        row = _load_row(d, discipline, 0.0, ctl, atl)
        # Readers key off `time` (that is what the stored rows carry), so give
        # the synthesized days the same midnight-UTC stamp write_daily_load uses.
        row["time"] = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        out.append(row)
        d += timedelta(days=1)
    return out
