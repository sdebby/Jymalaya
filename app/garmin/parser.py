"""
Parse raw Garmin activity summaries into Jymalaya `workouts` entries and split
brick (multi-sport) sessions into separate bike + run entries.

Output shape matches the PRD `workouts` measurement (assets/PRD.md section 5.1).
TSS / intensity_factor are left None here -- the metrics module fills them. This
module only does structural shaping; it never calls the network.

Brick handling (PRD: bricks are ALWAYS split): a multi-sport activity becomes one
`bike` entry and one `run` entry sharing a `session_id`, with `activity_id`
suffixed `_bike` / `_run` and `is_brick_split=True`, ordered by start time. Splitting
needs per-leg data; when a multi-sport parent carries its legs under `_legs`
(list of sub-activity summaries), we split on those, dropping transition (T1/T2)
legs. The Garmin client attaches `_legs` via fetch_brick_legs during the pull; a
parent still lacking leg data (enrichment failed) passes through as a single
flagged entry; standalone activities are normalized 1:1.
"""

from __future__ import annotations

import json
import uuid

from app.metrics.calculator import swim_pace_sec_per_100m

# Garmin activityType.typeKey -> Jymalaya discipline vocabulary
# (lowercase swim / bike / run / strength; everything else -> "other").
_SPORT_MAP = {
    "cycling": "bike",
    "road_biking": "bike",
    "mountain_biking": "bike",
    "gravel_cycling": "bike",
    "indoor_cycling": "bike",
    "virtual_ride": "bike",
    "cyclocross": "bike",
    "running": "run",
    "treadmill_running": "run",
    "trail_running": "run",
    "track_running": "run",
    "indoor_running": "run",
    "virtual_run": "run",
    "lap_swimming": "swim",
    "open_water_swimming": "swim",
    "swimming": "swim",
    "strength_training": "strength",
    "indoor_cardio": "strength",
    "yoga": "strength",
    "pilates": "strength",
}

# typeKeys that denote a brick / multi-sport parent.
_MULTISPORT_KEYS = {"multi_sport", "multisport", "transition"}


def _discipline(type_key: str | None) -> str:
    if not type_key:
        return "other"
    return _SPORT_MAP.get(type_key.lower(), "other")


def _f(value) -> float | None:
    """Coerce to float, treating 0/None/non-numeric as None."""
    try:
        if value is None:
            return None
        v = float(value)
        return v if v != 0 else None
    except (TypeError, ValueError):
        return None


def _pace_sec_per_km(speed_mps) -> float | None:
    s = _f(speed_mps)
    return round(1000.0 / s, 1) if s else None


def _moving_seconds(activity: dict) -> float | None:
    """
    Seconds actually spent moving, rest between sets/intervals excluded --
    what swim sTSS is charged over (assets/tss_rtss_calculation_spec.md
    section 4). Garmin expresses it two ways and neither is always populated:
    `movingDuration`, and (for pool swims) `averageSpeed`, which is measured
    over active lengths only, so distance/averageSpeed is the active time.
    Take the smaller of whichever are present -- a field that merely echoes the
    timer carries no rest information -- and never exceed the timer duration.
    """
    duration = _f(activity.get("duration"))
    candidates = [_f(activity.get("movingDuration"))]
    distance, speed = _f(activity.get("distance")), _f(activity.get("averageSpeed"))
    if distance and speed:
        candidates.append(distance / speed)
    known = [c for c in candidates if c] + ([duration] if duration else [])
    return round(min(known), 1) if known else None



def _te(value) -> float | None:
    """
    A Garmin Training Effect score (0.0-5.0), rounded to one decimal. Unlike
    _f, a real 0.0 is kept: "0.0 No Benefit" is a valid anaerobic answer.
    Missing, non-numeric or out-of-range values are None.
    """
    try:
        if value is None:
            return None
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, 1) if 0.0 <= v <= 5.0 else None


def _te_label(value) -> str | None:
    """Garmin's primary-benefit key (TEMPO, LACTATE_THRESHOLD, ...), as sent."""
    if not value:
        return None
    return str(value).strip().upper() or None


def _start_time(activity: dict) -> str:
    return activity.get("startTimeLocal") or activity.get("startTimeGMT") or ""


def _start_time_utc(activity: dict) -> str:
    """
    The activity's true UTC instant. `start_time` above is Garmin's local wall
    clock -- right for the athlete's calendar date, but it is not an instant, so
    it must never become the InfluxDB point timestamp on its own.
    """
    return activity.get("startTimeGMT") or ""


def _normalize(
    activity: dict,
    *,
    discipline: str | None = None,
    activity_id=None,
    session_id: str | None = None,
    is_brick_split: bool = False,
) -> dict:
    """Shape one (sub-)activity summary into a workout entry."""
    type_key = (activity.get("activityType") or {}).get("typeKey")
    disc = discipline or _discipline(type_key)

    speed = activity.get("averageSpeed")  # m/s
    start = _start_time(activity)
    hr_zones = activity.get("_hr_zones")
    # Only swims need moving time (sTSS charges rest-excluded hours); for bike
    # and run the timer duration is the basis, so we don't store a second one.
    moving_sec = _moving_seconds(activity) if disc == "swim" else None

    return {
        "activity_id": str(activity_id if activity_id is not None else activity.get("activityId")),
        "session_id": session_id,
        "discipline": disc,
        "garmin_type": type_key,
        "is_brick_split": is_brick_split,
        "title": activity.get("activityName"),
        "start_time": start,
        "start_time_utc": _start_time_utc(activity),
        "date": start.split(" ")[0] if start else "",
        "duration_sec": _f(activity.get("duration")),
        "moving_duration_sec": moving_sec,
        "distance_m": _f(activity.get("distance")),
        "avg_hr": _f(activity.get("averageHR")),
        "max_hr": _f(activity.get("maxHR")),
        "avg_power": _f(activity.get("avgPower") or activity.get("averagePower")),
        "normalized_power": _f(
            activity.get("normPower") or activity.get("normalizedPower")
        ),
        "avg_pace_sec_per_km": _pace_sec_per_km(speed) if disc != "swim" else None,
        # Swim pace is over moving time only (rest between sets excluded), the
        # NSS of assets/tss_rtss_calculation_spec.md section 4.
        "avg_pace_sec_per_100m": swim_pace_sec_per_100m(moving_sec, _f(activity.get("distance")))
        if disc == "swim"
        else None,
        "elevation_gain_m": _f(activity.get("elevationGain")),
        "elevation_loss_m": _f(activity.get("elevationLoss")),
        "calories": _f(activity.get("calories")),
        "max_power": _f(activity.get("maxPower")),
        # Garmin reports cadence under a per-sport key (rpm for bike, steps/min
        # for run, strokes/min for swim); take whichever the activity carries.
        "avg_cadence": _f(
            activity.get("averageBikingCadenceInRevPerMinute")
            or activity.get("averageRunningCadenceInStepsPerMinute")
            or activity.get("averageSwimCadenceInStrokesPerMinute")
        ),
        # Garmin's Training Effect, from the activity list row (each brick leg
        # carries its own). Display only: nothing in the metrics or the
        # Claude payloads reads them.
        "aerobic_te": _te(activity.get("aerobicTrainingEffect")),
        "anaerobic_te": _te(activity.get("anaerobicTrainingEffect")),
        "te_label": _te_label(activity.get("trainingEffectLabel")),
        # The athlete's own rating, from the FIT file's session message
        # (fit._rating): RPE 1-10 and feel 0-100, where 0 is a real "Very
        # Weak". None when the file was not fetched or carries no rating, so
        # a re-parse never blanks a stored one. Display only.
        "rpe": activity.get("_rpe"),
        "feel": activity.get("_feel"),
        # Runs with FIT samples (personal_api.enrich_with_fit): the rTSS basis.
        "ngp_sec_per_km": activity.get("_ngp_sec_per_km") if disc == "run" else None,
        # Samples / laps / planned workout for the sync to store next to the
        # point. Never a workout field: write_workouts ignores unknown keys.
        "_detail": activity.get("_detail"),
        # Filled by the metrics module:
        "tss": None,
        "tss_method": None,
        "intensity_factor": None,
        # JSON string per the PRD measurement schema; None when unavailable.
        "zones_time_sec": json.dumps(hr_zones) if hr_zones else None,
    }


def _stable_session_id(parent_id) -> str:
    """
    Deterministic UUID for a brick session, derived from the parent activity id so
    that re-syncing the same brick reuses the same `session_id` tag (and therefore
    overwrites the same InfluxDB points instead of creating duplicates). Falls back
    to a random UUID only when the parent has no id.
    """
    if parent_id is None:
        return str(uuid.uuid4())
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"tricoach-brick-{parent_id}"))


def leg_activity_id(parent_id, leg: dict) -> str:
    """
    The stored id of a brick leg: {parent}_{bike|run|swim}, or {parent}_leg for
    any other sport. Garmin does not know this id -- the leg's samples, laps and
    analysis are stored under it, so it never has to be resolved back.
    """
    disc = _discipline((leg.get("activityType") or {}).get("typeKey"))
    suffix = disc if disc in ("bike", "run", "swim") else "leg"
    return f"{parent_id}_{suffix}"


def _is_multisport(activity: dict) -> bool:
    type_key = ((activity.get("activityType") or {}).get("typeKey") or "").lower()
    return type_key in _MULTISPORT_KEYS or bool(activity.get("_legs"))


def split_bricks(activity: dict) -> list[dict]:
    """
    Split a multi-sport activity into separate discipline entries sharing a
    session_id. Legs come from `_legs` (sub-activity summaries). Bike and run legs
    get `activity_id` suffixed `_bike` / `_run`; any other leg keeps a discipline
    suffix. Entries are returned in chronological order.
    """
    legs = activity.get("_legs") or []
    parent_id = activity.get("activityId")
    session_id = str(activity.get("_session_id") or _stable_session_id(parent_id))

    if not legs:
        # Multi-sport parent without per-leg data: pass through, flagged so a later
        # enrichment pass can split it once leg activity ids are fetched.
        entry = _normalize(activity, session_id=session_id, is_brick_split=True)
        return [entry]

    entries = []
    for leg in legs:
        type_key = ((leg.get("activityType") or {}).get("typeKey") or "").lower()
        if "transition" in type_key:
            continue  # T1/T2 are not workouts -- only the real legs become entries
        entries.append(
            _normalize(
                leg,
                discipline=_discipline(type_key),
                activity_id=leg_activity_id(parent_id, leg),
                session_id=session_id,
                is_brick_split=True,
            )
        )
    entries.sort(key=lambda e: e.get("start_time") or "")
    return entries


def parse_activities(raw: list[dict]) -> list[dict]:
    """Normalize a list of raw Garmin activities, splitting bricks. Oldest first."""
    workouts: list[dict] = []
    for activity in raw or []:
        if _is_multisport(activity):
            workouts.extend(split_bricks(activity))
        else:
            workouts.append(_normalize(activity))
    workouts.sort(key=lambda w: w.get("start_time") or "")
    return workouts
