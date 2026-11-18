"""
Send to device: a plan session translated into intervals.icu's workout text
(app/intervals/workout.py). Pure -- no network.
"""

from __future__ import annotations

import pytest

from app.intervals.workout import (
    format_duration,
    session_to_event,
    step_target,
    workout_text,
)

PROFILE = {"ftp_w": 200, "css_sec_100m": 120, "lthr_bpm": 170}


def _steps(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("- ")]


# --- durations ---------------------------------------------------------------

@pytest.mark.parametrize(
    "minutes, expected",
    [(12, "12m"), (2.5, "2m30s"), (90, "1h30m"), (60, "1h"), (0.5, "30s")],
)
def test_duration_uses_intervals_units(minutes, expected):
    # "m" is minutes in intervals.icu (metres are "mtr").
    assert format_duration(minutes) == expected


@pytest.mark.parametrize("minutes", [None, 0, -5, "10", True])
def test_no_duration_for_nothing_usable(minutes):
    assert format_duration(minutes) is None


# --- targets -----------------------------------------------------------------

def test_run_targets_are_percent_lthr():
    assert step_target("run", "Z2", None, PROFILE) == "80-89% LTHR"
    # The prompt's open-ended Z5 is closed for the watch.
    assert step_target("run", "Z5", None, PROFILE) == "100-106% LTHR"


@pytest.mark.parametrize("zone", ["Z1-Z2", "z1-z2", "Z1–Z2", "Z2-Z1", "Z1-2"])
def test_zone_range_spans_both_bands(zone):
    assert step_target("run", zone, None, PROFILE) == "65-89% LTHR"


def test_run_target_needs_no_jymalaya_threshold():
    # intervals.icu applies its own LTHR to a % LTHR target.
    assert step_target("run", "Z3", None, {}) == "89-96% LTHR"


def test_unknown_zone_gets_no_target():
    assert step_target("run", "race pace", None, PROFILE) is None
    assert step_target("run", None, None, PROFILE) is None


def test_bike_uses_the_plan_watts():
    assert step_target("bike", "Z2", "106-143", PROFILE) == "106-143w"
    assert step_target("bike", "Z4", "190W", PROFILE) == "190w"


def test_bike_zero_low_bound_is_raised_to_the_floor():
    # 45% of a 200 W FTP.
    assert step_target("bike", "Z1", "0-104", PROFILE) == "90-104w"


def test_bike_falls_back_to_the_ftp_band():
    assert step_target("bike", "Z2", None, PROFILE) == "112-150w"
    assert step_target("bike", "Z3-Z4", "", PROFILE) == "152-210w"


def test_bike_without_ftp_or_watts_gets_no_target():
    assert step_target("bike", "Z2", None, {}) is None


def test_swim_pace_is_absolute_and_faster_first():
    # 2:00/100m CSS: Z2 is 1.10-1.21 x CSS = 2:12-2:25.
    assert step_target("swim", "Z2", None, PROFILE) == "2:12/100m-2:25/100m Pace"


def test_swim_without_css_gets_no_target():
    assert step_target("swim", "Z2", None, {"ftp_w": 200}) is None


def test_strength_is_time_only():
    assert step_target("strength", "Z2", None, PROFILE) is None


# --- workout text ------------------------------------------------------------

def _intervals(reps: int, *, closing_recovery: bool, last_zone: str = "Z5") -> dict:
    structure = [{"segment": "Warmup", "duration_min": 12, "zone": "Z1-Z2"}]
    for n in range(1, reps + 1):
        zone = last_zone if n == reps else "Z5"
        structure.append({"segment": f"Interval {n}", "duration_min": 3, "zone": zone})
        if n < reps or closing_recovery:
            structure.append({"segment": f"Recovery {n}", "duration_min": 3, "zone": "Z1"})
    structure.append({"segment": "Cooldown", "duration_min": 10, "zone": "Z1"})
    return {"discipline": "run", "type": "intervals", "structure": structure}


def _body(text: str) -> str:
    """The step section: between the heading and the benefit line."""
    return text.split("\n\n", 1)[1].split("\n\nBenefit", 1)[0].rstrip("\n")


def test_interval_set_is_sent_as_one_repeat():
    assert _body(workout_text(_intervals(5, closing_recovery=True), PROFILE)) == (
        "- Warmup 12m 65-89% LTHR intensity=warmup\n"
        "\n"
        "5x\n"
        "- Interval 3m 100-106% LTHR intensity=active\n"
        "- Recovery 3m 65-80% LTHR intensity=rest\n"
        "\n"
        "- Cooldown 10m 65-80% LTHR intensity=cooldown"
    )


def test_short_last_rep_follows_the_repeat_as_its_own_step():
    # 5 intervals, 4 recoveries: 5x would add a recovery the plan does not have.
    assert _body(workout_text(_intervals(5, closing_recovery=False), PROFILE)) == (
        "- Warmup 12m 65-89% LTHR intensity=warmup\n"
        "\n"
        "4x\n"
        "- Interval 3m 100-106% LTHR intensity=active\n"
        "- Recovery 3m 65-80% LTHR intensity=rest\n"
        "\n"
        "- Interval 3m 100-106% LTHR intensity=active\n"
        "- Cooldown 10m 65-80% LTHR intensity=cooldown"
    )


def test_a_different_last_rep_ends_the_repeat_before_it():
    text = workout_text(_intervals(4, closing_recovery=True, last_zone="Z4"), PROFILE)
    assert "\n3x\n" in text
    assert _steps(text)[-3:] == [
        "- Interval 3m 96-100% LTHR intensity=active",
        "- Recovery 3m 65-80% LTHR intensity=rest",
        "- Cooldown 10m 65-80% LTHR intensity=cooldown",
    ]


def test_two_sets_become_two_repeats():
    structure = [{"segment": "Warmup", "duration_min": 10, "zone": "Z1"}]
    for _ in range(3):
        structure += [
            {"segment": "Hard", "duration_min": 3, "zone": "Z5"},
            {"segment": "Easy", "duration_min": 2, "zone": "Z1"},
        ]
    for _ in range(2):
        structure += [
            {"segment": "Tempo", "duration_min": 8, "zone": "Z3"},
            {"segment": "Easy", "duration_min": 2, "zone": "Z1"},
        ]
    text = workout_text({"discipline": "run", "structure": structure}, PROFILE)
    assert "\n3x\n- Hard 3m" in text
    assert "\n2x\n- Tempo 8m" in text


def test_repeated_single_step_folds_without_a_pair():
    structure = [{"segment": f"Stride {n}", "duration_min": 0.5, "zone": "Z5"} for n in range(1, 7)]
    text = workout_text({"discipline": "run", "structure": structure}, PROFILE)
    assert _body(text) == "6x\n- Stride 30s 100-106% LTHR intensity=active"


def test_one_full_rep_and_a_short_one_stay_unrolled_with_clean_cues():
    session = {
        "discipline": "run",
        "structure": [
            {"segment": "Warmup", "duration_min": 12, "zone": "Z1-Z2"},
            {"segment": "Interval 1", "duration_min": 3, "zone": "Z5", "notes": "5:21/km x <1.00"},
            {"segment": "Recovery 1", "duration_min": 3, "zone": "Z1"},
            {"segment": "Interval 2", "duration_min": 3, "zone": "Z5"},
            {"segment": "Cooldown", "duration_min": 10, "zone": "Z1"},
        ],
    }
    assert _steps(workout_text(session, PROFILE)) == [
        "- Warmup 12m 65-89% LTHR intensity=warmup",
        "- Interval 3m 100-106% LTHR intensity=active",
        "- Recovery 3m 65-80% LTHR intensity=rest",
        "- Interval 3m 100-106% LTHR intensity=active",
        "- Cooldown 10m 65-80% LTHR intensity=cooldown",
    ]


def test_segment_notes_never_reach_a_step_line():
    # A pace like "5:21/km" in a step line would be parsed as its target.
    session = {
        "discipline": "run",
        "structure": [{"segment": "Tempo", "duration_min": 20, "zone": "Z3", "notes": "aim 5:40/km"}],
    }
    assert "5:40" not in workout_text(session, PROFILE)


@pytest.mark.parametrize(
    "name, cue",
    [
        ("Interval 1", "Interval"),
        ("Z2 main set", "main set"),
        ("Warm-up", "Warm up"),
        ("Strides (4 x 20s)", "Strides"),
        ("Race pace block", "Race block"),
        (None, ""),
    ],
)
def test_cue_drops_anything_the_parser_could_misread(name, cue):
    session = {"discipline": "strength", "structure": [{"segment": name, "duration_min": 5}]}
    (line,) = _steps(workout_text(session, PROFILE))
    assert line.split(" intensity=")[0] == (f"- {cue} 5m" if cue else "- 5m")


def test_segments_without_a_duration_are_skipped():
    session = {
        "discipline": "run",
        "structure": [
            {"segment": "Warmup", "zone": "Z1"},
            {"segment": "Main", "duration_min": 30, "zone": "Z2"},
        ],
    }
    assert _steps(workout_text(session, PROFILE)) == ["- Main 30m 80-89% LTHR intensity=active"]


def test_description_is_heading_steps_and_benefit_only():
    session = {
        "discipline": "run",
        "type": "intervals",
        "notes": "Long paragraph with 5:21/km x 1.14 working.",
        "structure": [
            {"segment": "Warmup", "duration_min": 10, "zone": "Z1"},
            {"segment": "Interval", "duration_min": 5, "zone": "Z5"},
            {"segment": "Cooldown", "duration_min": 10, "zone": "Z1"},
        ],
    }
    text = workout_text(session, PROFILE)
    assert text == (
        "Run intervals\n\n"
        "- Warmup 10m 65-80% LTHR intensity=warmup\n"
        "- Interval 5m 100-106% LTHR intensity=active\n"
        "- Cooldown 10m 65-80% LTHR intensity=cooldown\n"
        "\nBenefit - VO2max increase\n"
    )
    assert "5:21" not in text


def test_benefit_follows_the_session_type():
    def text(**extra):
        return workout_text(
            {"discipline": "bike", "structure": [{"segment": "Main", "duration_min": 60, "zone": "Z2"}], **extra},
            PROFILE,
        )

    assert "Benefit - aerobic base" in text(type="endurance")
    assert "Benefit" not in text()  # no type, no claim
    assert text().startswith("Bike\n\n")


def test_no_structure_falls_back_to_one_step():
    one_zone = {"discipline": "run", "duration_min": 45, "zones_target": {"Z2": "100%"}}
    assert _steps(workout_text(one_zone, PROFILE)) == ["- 45m 80-89% LTHR intensity=active"]
    mixed = {"discipline": "run", "duration_min": 45, "zones_target": {"Z1": "50%", "Z2": "50%"}}
    assert _steps(workout_text(mixed, PROFILE)) == ["- 45m intensity=active"]


# --- step types (Garmin's Warm Up / Run / Rest / Cool Down) -------------------

def _intensities(text: str) -> list[str]:
    return [line.rsplit("intensity=", 1)[1] for line in _steps(text)]


def _cruise_intervals(discipline: str) -> dict:
    """The 12 Oct threshold cruise intervals: 15 warm-up, 2 x (10 hard, 3 easy), 10 cool-down."""
    return {
        "discipline": discipline,
        "type": "threshold",
        "structure": [
            {"segment": "Warm-up", "duration_min": 15, "zone": "Z1-Z2"},
            {"segment": "Interval 1", "duration_min": 10, "zone": "Z4"},
            {"segment": "Recovery 1", "duration_min": 3, "zone": "Z1"},
            {"segment": "Interval 2", "duration_min": 10, "zone": "Z4"},
            {"segment": "Recovery 2", "duration_min": 3, "zone": "Z1"},
            {"segment": "Cool-down", "duration_min": 10, "zone": "Z1"},
        ],
    }


@pytest.mark.parametrize("discipline", ["run", "bike", "swim"])
def test_cruise_intervals_get_warmup_work_rest_cooldown(discipline):
    text = workout_text(_cruise_intervals(discipline), PROFILE)
    assert _intensities(text) == ["warmup", "active", "rest", "cooldown"]
    # The two reps still fold into one repeat, every line tagged.
    assert "\n2x\n- Interval 10m " in text
    body = _body(text).split("\n")
    assert body[0].endswith("intensity=warmup")
    assert body[3].endswith("intensity=active") and body[4].endswith("intensity=rest")


def test_tags_follow_the_target():
    text = workout_text(_cruise_intervals("bike"), PROFILE)
    assert "- Interval 10m 182-210w intensity=active" in text


def test_unnamed_steps_take_their_type_from_zone_and_place():
    structure = [
        {"segment": "Start", "duration_min": 10, "zone": "Z1"},
        {"segment": "", "duration_min": 4, "zone": "Z5"},
        {"segment": "", "duration_min": 2, "zone": "Z1"},
        {"segment": "", "duration_min": 4, "zone": "Z5"},
        {"segment": "End", "duration_min": 10, "zone": "Z1-Z2"},
    ]
    text = workout_text({"discipline": "run", "structure": structure}, PROFILE)
    assert _intensities(text) == ["warmup", "active", "rest", "active", "cooldown"]


def test_hard_and_easy_names_are_active_and_rest():
    structure = [{"segment": "Warmup", "duration_min": 10, "zone": "Z1"}]
    for _ in range(3):
        structure += [
            {"segment": "Hard", "duration_min": 3, "zone": "Z5"},
            {"segment": "Easy", "duration_min": 2, "zone": "Z1"},
        ]
    text = workout_text({"discipline": "run", "structure": structure}, PROFILE)
    # The last "Easy" is still the set's recovery, so the three reps fold.
    assert _body(text).startswith("- Warmup 10m 65-80% LTHR intensity=warmup\n\n3x\n")
    assert _intensities(text) == ["warmup", "active", "rest"]


@pytest.mark.parametrize(
    "structure",
    [
        [{"segment": "Easy run", "duration_min": 50, "zone": "Z2"}],
        [{"segment": "Recovery run", "duration_min": 30, "zone": "Z1"}],
        [
            {"segment": "Easy", "duration_min": 40, "zone": "Z1-Z2"},
            {"segment": "Steady", "duration_min": 20, "zone": "Z2"},
        ],
    ],
)
def test_easy_sessions_are_never_rest_or_warmup(structure):
    text = workout_text({"discipline": "run", "structure": structure}, PROFILE)
    assert set(_intensities(text)) == {"active"}


def test_rest_between_strength_sets():
    structure = [
        {"segment": "Squats", "duration_min": 5},
        {"segment": "Rest", "duration_min": 2},
        {"segment": "Lunges", "duration_min": 5},
    ]
    text = workout_text({"discipline": "strength", "structure": structure}, PROFILE)
    assert _intensities(text) == ["active", "rest", "active"]


def test_nothing_to_send_raises():
    with pytest.raises(ValueError):
        workout_text({"discipline": "run", "structure": [{"segment": "Main"}]}, PROFILE)


# --- the event ---------------------------------------------------------------

def test_event_shape():
    session = {"discipline": "bike", "title": "Aerobic ride", "duration_min": 60}
    event = session_to_event("2026-10-03", 1, session, PROFILE)
    assert event["category"] == "WORKOUT"
    assert event["type"] == "Ride"
    assert event["name"] == "Aerobic ride"
    # The second session of the day sits an hour after the first.
    assert event["start_date_local"] == "2026-10-03T08:00:00"
    # Upsert key: re-sending this plan slot updates the same calendar entry.
    assert event["external_id"] == "jymalaya-2026-10-03-1"


@pytest.mark.parametrize(
    "discipline, sport",
    [("swim", "Swim"), ("bike", "Ride"), ("run", "Run"), ("strength", "WeightTraining")],
)
def test_sport_types(discipline, sport):
    event = session_to_event("2026-10-03", 0, {"discipline": discipline, "duration_min": 30}, PROFILE)
    assert event["type"] == sport
    assert event["name"] == f"{discipline.capitalize()} session"


def test_unknown_discipline_raises():
    with pytest.raises(ValueError):
        session_to_event("2026-10-03", 0, {"discipline": "yoga", "duration_min": 30}, PROFILE)
