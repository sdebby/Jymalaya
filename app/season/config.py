"""
Every tunable number of the season plan, as named constants.

Nothing here is inline in the builder: the per-distance tables are coaching
policy the athlete may want to tune, and the phase table is pinned to the
prompt files by tests/test_season_phase_consistency.py.
"""

from __future__ import annotations

# Phase boundaries in WEEKS OUT from the race, copied from the phase lines of
# prompts/<Distance>_Personalization.txt -- the source of truth Claude already
# reads in rule 9, so the season plan and the 7-day plan always name the same
# phase for a week (the athlete's call, 2026-10-23, over the handoff's percent
# split). A week's weeks-out is (race_date - week_start).days // 7, so the race
# week is 0 -- the same floor arithmetic as _goal_block's weeks_until_race.
#
#   taper  weeks_out <  taper   (the number of taper weeks, race week included)
#   peak   weeks_out <  peak
#   build  weeks_out <= build
#   base   weeks_out >  build
#
# The taper length is each file's phase line, not its taper block (the athlete's
# call): 70.3 tapers 4 weeks, 140.6 5 weeks, custom 4.
PHASE_WEEKS_OUT: dict[str, dict[str, int]] = {
    "sprint": {"taper": 2, "peak": 8, "build": 14},
    "olympic": {"taper": 2, "peak": 8, "build": 14},
    "70.3": {"taper": 4, "peak": 12, "build": 20},
    "140.6": {"taper": 5, "peak": 14, "build": 24},
    "marathon": {"taper": 3, "peak": 8, "build": 16},
    "half-marathon": {"taper": 2, "peak": 7, "build": 14},
    "15K": {"taper": 2, "peak": 6, "build": 12},
    "10K": {"taper": 2, "peak": 6, "build": 10},
    "5K": {"taper": 1, "peak": 4, "build": 8},
    "custom": {"taper": 4, "peak": 8, "build": 16},
}
DEFAULT_EVENT_TYPE = "custom"

# Taper weeks as a fraction of the highest loading week, keyed by the number of
# taper weeks; the last factor is the race week, whose target counts only the
# sessions before the race. The final weeks match the files' taper blocks; the
# earlier weeks of a long taper phase (70.3, 140.6) step down gently. A plan
# with fewer taper weeks than its type asks for uses the tail of the list.
TAPER_FACTORS: dict[int, list[float]] = {
    1: [0.50],
    2: [0.60, 0.40],
    3: [0.75, 0.55, 0.40],
    4: [0.85, 0.75, 0.60, 0.40],
    5: [0.90, 0.80, 0.70, 0.50, 0.35],
}

# Peak CTL per distance: defaults, prefilled and editable when a plan is created.
# The peak weekly load is PEAK_WEEKLY_FACTOR x peak CTL -- the steady state CTL
# approaches, so the projected CTL at the end of the peak stays honestly lower.
PEAK_CTL: dict[str, float] = {
    "sprint": 60,
    "olympic": 72,
    "70.3": 90,
    "140.6": 115,
    "marathon": 70,
    "half-marathon": 62,
    "15K": 58,
    "10K": 52,
    "5K": 47,
    "custom": 65,
}
PEAK_WEEKLY_FACTOR = 7

# Weekly growth of the loading path, per build-up pace.
RAMP_GROWTH: dict[str, float] = {"gentle": 0.05, "standard": 0.08, "ambitious": 0.10}
DEFAULT_RAMP = "standard"
# Hard cap on week-over-week growth, matching the prompt files' "never raise
# weekly volume more than 10% above the previous week".
MAX_WEEKLY_GROWTH = 0.10

# The athlete's activity class (0 inactive .. 10 competitive, the profile's
# slider) picks the defaults the create form leaves blank, below the coach
# instructions and above the constants here. Bands as (highest class, ramp,
# loading weeks before a recovery week, factor on the distance's PEAK_CTL).
# Class 4, the profile default, is the "standard" band: an athlete who never
# moved the slider gets exactly the plan the constants alone would give.
ACTIVITY_CLASS_BANDS: list[tuple[int, str, int, float]] = [
    (3, "gentle", 2, 0.85),
    (6, "standard", 3, 1.00),
    (10, "ambitious", 3, 1.10),
]
# The first loading week's floor for an athlete with little history:
# START_FLOOR_BASE + START_FLOOR_PER_CLASS x class (class 4 = MIN_START_TSS).
START_FLOOR_BASE = 90
START_FLOOR_PER_CLASS = 15


def _clean_class(activity_class) -> int | None:
    if isinstance(activity_class, bool) or not isinstance(activity_class, (int, float)):
        return None
    return max(0, min(10, int(activity_class)))


def activity_band(activity_class) -> tuple[str, int, float] | None:
    """(ramp, recovery cadence, peak CTL factor) for a class, None without one."""
    c = _clean_class(activity_class)
    if c is None:
        return None
    for top, ramp, every, peak_factor in ACTIVITY_CLASS_BANDS:
        if c <= top:
            return ramp, every, peak_factor
    return None


def start_floor(activity_class) -> float:
    """The new-athlete start floor for a class; MIN_START_TSS without one."""
    c = _clean_class(activity_class)
    if c is None:
        return MIN_START_TSS
    return START_FLOOR_BASE + START_FLOOR_PER_CLASS * c


# 3 loading weeks, then 1 recovery week at 70% of the last loading week.
RECOVERY_CADENCE_WEEKS = 3
RECOVERY_FACTOR = 0.70

# Fitness mode steps (percent).
STEP_INCREASE_PCT = 7
STEP_DECREASE_PCT = 12

# A return week after a layoff of LAYOFF_DAYS or more, at this share of the
# last loading week.
RETURN_FACTOR = 0.60
LAYOFF_DAYS = 7

# History fallbacks: hours are derived from TSS at the athlete's own rate, and
# a new athlete starts no lower than MIN_START_TSS.
DEFAULT_TSS_PER_HOUR = 55.0
MIN_START_TSS = 150
MIN_HISTORY_WEEKS = 2

# Fatigue bands from TSB, after the Friel zones the Fatigue Zones chart draws
# (frontend/src/formZones.ts) so the two never name the same form differently.
BAND_FRESH_MIN = 5  # fresh: tsb >= +5
BAND_OK_MIN = -10  # ok: -10 <= tsb < +5
BAND_TIRED_MIN = -30  # tired: -30 <= tsb < -10; high below
BAND_HIGH_REPEAT = -20  # also high: below this at two check-ins in a row

# Event-mode fatigue gate.
GATE_LOWER_TSB = -30  # next unpinned loading week x GATE_LOWER_FACTOR
GATE_LOWER_FACTOR = 0.80
GATE_RECOVERY_TSB = -40  # current week remaining x 0.5, next week recovery
GATE_RECOVERY_FACTOR = 0.50
MID_WEEK_BANNER_TSB = -30

# The plan's weekly sum may sit this far either side of the season target.
TARGET_TOLERANCE = 0.10

# Each summary line stays under this many characters.
SUMMARY_MAX_CHARS = 160

# Session focus text per phase / kind, shown in the week tooltip.
FOCUS_TEXT: dict[str, str] = {
    "base": "Aerobic volume and technique",
    "build": "Threshold blocks",
    "peak": "Race-pace work",
    "taper": "Cut volume, keep intensity",
    "race": "Race week",
    "recovery": "Absorb the load",
    "return": "Ease back in",
}
# A sub event's weeks, with its name filled in.
SUB_FOCUS_TEXT: dict[str, str] = {
    "pre": "Mini-taper for {name}",
    "race": "{name} race week",
    "post": "Recover from {name}",
}

# Sync adaptation (app/season/adapt.py).
UNDER_DELIVERY_PCT = 0.80  # two completed loading weeks below this share of target
OVER_DELIVERY_PCT = 1.15  # last week above this share of target ...
OVER_DELIVERY_TSB = -10  # ... with TSB below this: hold, no increase
FRESH_BONUS_TSB = 10  # TSB at or above this ...
FRESH_BONUS_DONE_PCT = 0.95  # ... after a week at this share of target or more
FRESH_BONUS_PCT = 0.05  # raises the week by this much, never past the 10% cap

# Swim / bike / run split of a triathlon week, as integer percents of the
# week's TSS target (the athlete's call, 2026-10-25): a code table per distance
# and phase, prefilled and editable when a plan is created (stored as the
# plan's settings.split_by_phase) and editable per week. These are TSS shares,
# not time shares -- a run earns more TSS per hour than a ride, so the run share
# sits a little above the usual time split. Long courses move a few points from
# swim to bike through build and peak; the taper (and the race week) goes back
# to the base mix. Strength is support work and gets no share: it counts toward
# the week's total only.
TRI_TYPES = ("sprint", "olympic", "70.3", "140.6")
SPLIT_DISCIPLINES = ("swim", "bike", "run")
SPLIT_PHASES = ("base", "build", "peak", "taper")
DISCIPLINE_SPLIT: dict[str, dict[str, dict[str, int]]] = {
    "sprint": {
        "base": {"swim": 20, "bike": 40, "run": 40},
        "build": {"swim": 18, "bike": 41, "run": 41},
        "peak": {"swim": 17, "bike": 41, "run": 42},
        "taper": {"swim": 20, "bike": 40, "run": 40},
    },
    "olympic": {
        "base": {"swim": 20, "bike": 43, "run": 37},
        "build": {"swim": 18, "bike": 45, "run": 37},
        "peak": {"swim": 17, "bike": 46, "run": 37},
        "taper": {"swim": 20, "bike": 43, "run": 37},
    },
    "70.3": {
        "base": {"swim": 17, "bike": 50, "run": 33},
        "build": {"swim": 15, "bike": 52, "run": 33},
        "peak": {"swim": 14, "bike": 53, "run": 33},
        "taper": {"swim": 17, "bike": 50, "run": 33},
    },
    "140.6": {
        "base": {"swim": 15, "bike": 53, "run": 32},
        "build": {"swim": 13, "bike": 56, "run": 31},
        "peak": {"swim": 12, "bike": 57, "run": 31},
        "taper": {"swim": 15, "bike": 53, "run": 32},
    },
}
# A fitness plan with the triathlon focus has one phase.
FITNESS_TRI_SPLIT: dict[str, int] = {"swim": 20, "bike": 45, "run": 35}

# The 7-day plan's sessions of one discipline may sit this far either side of
# its share of the week (rule 29), never flagged under SPLIT_MIN_TSS -- a 30 TSS
# swim budget is not a miss over a few TSS.
SPLIT_TOLERANCE = 0.15
SPLIT_MIN_TSS = 10

# Sub events (2026-11-14): races before the main event that the plan eases off
# around. Each factor multiplies the last loading week's target: "pre" is the
# week before the sub event's week (a long race only), "race" the week it falls
# in (its own TSS counts toward it), "post" the week after (a recovery week).
# The main event stays the goal: a sub event never changes a week's phase.
SUB_EVENT_TAPER: dict[str, dict[str, float | None]] = {
    "5K": {"pre": None, "race": 0.80, "post": None},
    "10K": {"pre": None, "race": 0.80, "post": None},
    "15K": {"pre": None, "race": 0.80, "post": None},
    "sprint": {"pre": None, "race": 0.80, "post": None},
    "olympic": {"pre": None, "race": 0.70, "post": None},
    "half-marathon": {"pre": None, "race": 0.70, "post": None},
    "custom": {"pre": None, "race": 0.70, "post": None},
    "marathon": {"pre": 0.85, "race": 0.60, "post": 0.60},
    "70.3": {"pre": 0.85, "race": 0.60, "post": 0.60},
    "140.6": {"pre": 0.85, "race": 0.60, "post": 0.60},
}
