"""Unit tests for the metrics calculator (pure functions, no network)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.metrics.calculator import (
    compute_load,
    daily_tss,
    estimate_tss,
    pad_load_to,
)

PROFILE = {
    "ftp_w": 250,
    "run_threshold_pace_sec_km": 270,
    "css_sec_100m": 95,
    "lthr_bpm": 162,
}


def test_ctss_power_based():
    # 1h at NP 225 vs FTP 250 -> IF 0.9 -> TSS 81.0
    w = {"discipline": "bike", "duration_sec": 3600, "normalized_power": 225}
    tss, intensity = estimate_tss(w, PROFILE)
    assert intensity == 0.9
    assert tss == 81.0


def test_ctss_falls_back_to_avg_power():
    w = {"discipline": "bike", "duration_sec": 3600, "avg_power": 250}
    tss, intensity = estimate_tss(w, PROFILE)
    assert intensity == 1.0
    assert tss == 100.0


def test_rtss_pace_based_at_threshold():
    # 1h exactly at threshold pace -> IF 1.0 -> TSS 100
    w = {"discipline": "run", "duration_sec": 3600, "avg_pace_sec_per_km": 270}
    tss, intensity = estimate_tss(w, PROFILE)
    assert intensity == 1.0
    assert tss == 100.0


def test_rtss_slower_than_threshold():
    # slower pace (300 > 270) -> IF 0.9 -> TSS 81
    w = {"discipline": "run", "duration_sec": 3600, "avg_pace_sec_per_km": 300}
    tss, intensity = estimate_tss(w, PROFILE)
    assert intensity == 0.9
    assert tss == 81.0


def test_stss_css_based_cubes_intensity():
    # 30 min moving, pace 100 vs CSS 95 -> IF 0.95; swim cubes IF
    # (TrainingPeaks sTSS) -> 0.5h * 0.95^3 * 100 = 42.9
    w = {"discipline": "swim", "duration_sec": 1800, "avg_pace_sec_per_100m": 100}
    tss, intensity = estimate_tss(w, PROFILE)
    assert intensity == 0.95
    assert tss == 42.9


def test_stss_charges_moving_time_not_the_timer():
    # Same swim with 6 min of rest in the pool: sTSS is defined over moving
    # time, so the rest neither slows the pace nor adds hours.
    w = {
        "discipline": "swim",
        "duration_sec": 2160,
        "moving_duration_sec": 1800,
        "avg_pace_sec_per_100m": 100,
    }
    tss, intensity = estimate_tss(w, PROFILE)
    assert intensity == 0.95
    assert tss == 42.9


def test_stss_matches_the_spec_worked_example():
    # assets/tss_rtss_calculation_spec.md section 4: swim FTP 75 m/min,
    # 1600 m in 23.17 min moving -> sTSS ~= 30.1. 75 m/min = 80 s/100m.
    moving = 23.17 * 60
    w = {
        "discipline": "swim",
        "duration_sec": moving,
        "moving_duration_sec": moving,
        "avg_pace_sec_per_100m": moving / 1600 * 100,
    }
    tss, intensity = estimate_tss(w, {"css_sec_100m": 80})
    assert intensity == pytest.approx(0.92, abs=0.005)
    assert tss == pytest.approx(30.1, abs=0.2)


def test_stss_hr_fallback_stays_squared_over_full_duration():
    # A swim without pace uses the HR fallback, which keeps the IF^2 form and
    # charges the whole session (hrTSS counts time at a heart rate, rest too).
    w = {"discipline": "swim", "duration_sec": 3600, "moving_duration_sec": 1800, "avg_hr": 162}
    tss, intensity = estimate_tss(w, PROFILE)
    assert intensity == 1.0
    assert tss == 100.0


def test_hr_fallback_for_strength():
    # strength has no power/pace -> HR fallback IF = 144/162
    w = {"discipline": "strength", "duration_sec": 3600, "avg_hr": 144}
    tss, intensity = estimate_tss(w, PROFILE)
    assert intensity == pytest.approx(144 / 162, abs=1e-3)
    assert tss == pytest.approx(79.0, abs=0.1)


def test_missing_data_returns_none():
    assert estimate_tss({"discipline": "bike", "duration_sec": 3600}, PROFILE) == (None, None)
    assert estimate_tss({"discipline": "bike", "normalized_power": 200}, PROFILE) == (None, None)


def test_daily_tss_aggregates_by_date_and_discipline():
    workouts = [
        {"date": "2026-06-20", "discipline": "bike", "tss": 80},
        {"date": "2026-06-20", "discipline": "run", "tss": 40},
        {"date": "2026-06-21", "discipline": "run", "tss": 50},
    ]
    assert daily_tss(workouts) == {"2026-06-20": 120, "2026-06-21": 50}
    assert daily_tss(workouts, "run") == {"2026-06-20": 40, "2026-06-21": 50}


def test_compute_load_single_day_matches_linear_ewma():
    # assets/Jymalaya_AI-rest_day_TSS.md: L_today = L_yest + (TSS - L_yest)/tau,
    # seeded from zero. `through` keeps the series from running on to today.
    day = date(2026, 6, 20)
    rows = compute_load({"2026-06-20": 100.0}, through=day)
    assert len(rows) == 1
    assert rows[0]["ctl"] == round(100 / 42, 1)
    assert rows[0]["atl"] == round(100 / 7, 1)
    assert rows[0]["tsb"] == round(100 / 42 - 100 / 7, 1)


def test_rest_day_matches_the_docs_worked_example():
    # The doc's example: CTL 80 / ATL 90 into a rest day -> 78.1 / 77.1.
    day = date(2026, 6, 20)
    rows = compute_load({"2026-06-20": 0.0}, seed_ctl=80.0, seed_atl=90.0, through=day)
    assert rows[0]["ctl"] == 78.1
    assert rows[0]["atl"] == 77.1


def test_compute_load_fills_gaps_and_spans_range():
    # two days three apart -> continuous 4-day series
    rows = compute_load(
        {"2026-06-20": 100.0, "2026-06-23": 100.0}, through=date(2026, 6, 23)
    )
    assert [r["date"] for r in rows] == [
        "2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23",
    ]
    assert rows[1]["tss"] == 0.0 and rows[2]["tss"] == 0.0
    # ATL decays then climbs; always trails nothing-special but stays finite
    assert all(r["ctl"] >= 0 for r in rows)


def test_trailing_rest_days_decay_through_today():
    # The bug this guards: a series that stopped at the last workout froze ATL
    # at its peak, so fatigue never recovered and form never rose.
    last_workout = date.today() - timedelta(days=5)
    rows = compute_load({last_workout.isoformat(): 100.0})

    assert len(rows) == 6
    assert rows[-1]["date"] == date.today().isoformat()
    assert all(r["tss"] == 0.0 for r in rows[1:])
    # Fatigue falls away faster than fitness (tau 7 vs 42), so form trends up.
    assert [r["atl"] for r in rows] == sorted((r["atl"] for r in rows), reverse=True)
    assert rows[-1]["atl"] < rows[0]["atl"]
    assert rows[-1]["ctl"] < rows[0]["ctl"]
    assert rows[-1]["ctl"] - rows[0]["ctl"] > rows[-1]["atl"] - rows[0]["atl"]
    assert rows[-1]["tsb"] > rows[0]["tsb"]


def test_rest_streak_is_iterated_day_by_day():
    # Every day of the streak gets its own row (no multi-day decay shortcut),
    # which is what makes the daily TSB trackable.
    start = date.today() - timedelta(days=9)
    rows = compute_load({start.isoformat(): 200.0})
    dates = [r["date"] for r in rows]
    assert dates == [(start + timedelta(days=i)).isoformat() for i in range(10)]


def test_through_only_ever_extends_the_series():
    # A `through` before the last workout must not truncate it.
    rows = compute_load(
        {"2026-06-20": 100.0, "2026-06-23": 100.0}, through=date(2026, 6, 21)
    )
    assert rows[-1]["date"] == "2026-06-23"


def test_compute_load_empty_stays_empty():
    # A discipline with no workouts writes no rows, even though `through` is set.
    assert compute_load({}, discipline="swim", through=date.today()) == []


# --- pad_load_to ----------------------------------------------------------
def _stored(day: date, ctl: float, atl: float, discipline: str = "all") -> dict:
    """A row shaped like one read back from InfluxDB (`time`, no `date`)."""
    return {
        "time": datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
        "discipline": discipline,
        "tss": 0.0,
        "ctl": ctl,
        "atl": atl,
        "tsb": round(ctl - atl, 1),
    }


def test_pad_load_to_is_a_noop_on_empty():
    assert pad_load_to([]) == []


def test_pad_load_to_is_a_noop_when_already_current():
    rows = [_stored(date.today(), 50.0, 60.0)]
    assert pad_load_to(rows) == rows


def test_pad_load_to_appends_one_row_per_missing_day():
    rows = [_stored(date.today() - timedelta(days=3), 50.0, 60.0)]
    padded = pad_load_to(rows)
    assert len(padded) == 4
    assert padded[-1]["date"] == date.today().isoformat()
    # Fatigue recovers across the gap; the stored row is left untouched.
    assert padded[-1]["atl"] < rows[0]["atl"]
    assert padded[-1]["tsb"] > rows[0]["tsb"]
    assert padded[0] is rows[0]


def test_pad_load_to_tracks_compute_load_over_the_same_gap():
    # The padder and the sync-time walk must not drift apart. They are not bit
    # identical: the padder resumes from the stored 1-decimal value, so it can
    # sit one rounding step off -- an error that decays rather than compounds.
    last_workout = date.today() - timedelta(days=4)
    full = compute_load({last_workout.isoformat(): 100.0})
    stored = [dict(full[0], time=datetime(
        last_workout.year, last_workout.month, last_workout.day, tzinfo=timezone.utc
    ))]
    padded = pad_load_to(stored)
    assert [r["date"] for r in padded] == [r["date"] for r in full]
    for got, want in zip(padded, full):
        # Both are 1-decimal values, so compare at that resolution: at most one
        # step apart. (Plain floats would trip over 12.3 - 12.2 > 0.1.)
        assert round(abs(got["ctl"] - want["ctl"]), 1) <= 0.1
        assert round(abs(got["atl"] - want["atl"]), 1) <= 0.1


def test_pad_load_to_is_idempotent_and_keeps_the_discipline():
    rows = [_stored(date.today() - timedelta(days=2), 40.0, 30.0, discipline="bike")]
    once = pad_load_to(rows)
    assert pad_load_to(once) == once
    assert all(r["discipline"] == "bike" for r in once)


def test_pad_load_to_never_writes_past_today():
    # daily_load reads stop at now(), so a future row would simply vanish.
    padded = pad_load_to([_stored(date.today() - timedelta(days=1), 50.0, 60.0)])
    assert padded[-1]["date"] == date.today().isoformat()
