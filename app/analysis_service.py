"""
Post-workout analysis (PRD section 7.9): read one completed activity's stored
detail (samples, laps, planned workout) from InfluxDB, derive the coaching
metrics, and have Claude review it. No Garmin call: the sync stored that detail
from the workout's FIT file (app/garmin/personal_api.py, app/workout_detail.py).

Shaped like recommend_service: build a payload, call Claude, stamp `_meta`, write
a JSON file under data/. The difference is the cache key. A plan is about the
future and goes stale every day; an analysis is about a session that already
happened and never changes, so there is no freshness check -- the cached file
stands until the athlete asks for a re-analysis.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.ai.claude import generate_analysis
from app.ai.prompt_builder import build_analysis_payload, load_stored_workout
from app.config import ROOT, get_athlete_profile
from app.influx.client import InfluxRepo
from app.metrics.analysis import build_derived_metrics, power_zone_split
from app.workout_detail import bucket_stream, stored_laps, stored_planned, stored_samples

log = logging.getLogger("jymalaya.analysis")

# One file per activity. Not InfluxDB: it holds no per-activity series, and a
# multi-KB JSON blob is a poor fit for a field -- the same reasoning that makes
# data/latest_recommendation.json the plan's source of truth.
ANALYSIS_DIR = ROOT / "data" / "analyses"


class AnalysisError(RuntimeError):
    """Raised with a user-facing message when an analysis cannot be produced."""


class DetailNotStored(AnalysisError):
    """The workout has no stored samples (synced before FIT samples were kept)."""


def _path(activity_id: str) -> Path:
    return ANALYSIS_DIR / f"{activity_id}.json"


def cached_analysis(activity_id: str) -> dict | None:
    """The stored analysis for this activity, or None when there isn't one."""
    try:
        return json.loads(_path(activity_id).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def clear_analyses() -> str:
    """Delete every cached analysis (called by the full data reset)."""
    if not ANALYSIS_DIR.is_dir():
        return "absent"
    removed = 0
    try:
        for f in ANALYSIS_DIR.glob("*.json"):
            f.unlink()
            removed += 1
    except OSError as e:
        return f"failed ({e})"
    return f"deleted ({removed})" if removed else "absent"


def _fetch_detail(repo: InfluxRepo, workout: dict) -> tuple[dict | None, list[dict], dict | None]:
    """
    The stored sample stream (bucketed for the prompt), the laps, and the
    structured workout it was run against. A workout with no stored samples is
    refused rather than analyzed half-blind: its detail was never kept, and the
    backfill script is the way to fetch it.
    """
    activity_id = str(workout.get("activity_id"))
    rows = stored_samples(repo, activity_id)
    if not rows:
        raise DetailNotStored(
            "No detailed data is stored for this workout. It was synced before "
            "workout samples were kept; run `python scripts/backfill_fit.py "
            "--days N --apply` with N reaching back to it, then analyze again."
        )
    stream = bucket_stream(rows, workout.get("duration_sec"))
    laps = stored_laps(repo, activity_id)
    planned = stored_planned(repo, activity_id)
    log.debug(
        "Stored detail for %s: %d samples, %d laps, planned workout %s",
        activity_id, len(rows), len(laps), "yes" if planned else "no",
    )
    return stream, laps, planned


def analyze_workout(activity_id: str, *, force: bool = False, trigger: str = "manual") -> dict:
    """
    Analyze one completed workout, returning the cached result unless `force`.

    Raises AnalysisError when the workout or its detail is not stored, and
    ClaudeError (from generate_analysis) when the model call fails; the
    router maps both to HTTP status codes.
    """
    if not force:
        cached = cached_analysis(activity_id)
        if cached is not None:
            log.debug("Serving cached analysis for %s", activity_id)
            return cached

    repo = InfluxRepo()
    try:
        workout = load_stored_workout(repo, activity_id)
        if workout is None:
            raise AnalysisError(f"No stored workout with activity_id {activity_id}.")
        stream, laps, planned = _fetch_detail(repo, workout)
    finally:
        repo.close()
    derived = build_derived_metrics(workout, stream, get_athlete_profile())

    payload = build_analysis_payload(
        workout,
        stream=stream,
        laps=laps,
        planned_workout=planned,
        derived_metrics=derived,
    )
    analysis = generate_analysis(payload)

    analysis["_meta"] = {
        "analysis_id": uuid.uuid4().hex,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "activity_id": activity_id,
        "trigger": trigger,
    }

    try:
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        _path(activity_id).write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    except OSError:
        # The analysis is still worth returning; only the cache is lost.
        log.warning("Could not cache the analysis for %s", activity_id, exc_info=True)
    return analysis


def workout_streams(activity_id: str) -> dict:
    """
    The stored samples of one workout for the modal's graphs: the same
    bucketed stream the analysis reads (30 s, widened to at most MAX_BUCKETS),
    plus the time in each %FTP power zone for a ride. Power zones are bike only:
    the profile has no running FTP, and bike bands would misread run power.

    Raises AnalysisError when the workout is unknown and DetailNotStored when it
    has no samples. No Claude or Garmin call.
    """
    repo = InfluxRepo()
    try:
        workout = load_stored_workout(repo, activity_id)
        if workout is None:
            raise AnalysisError(f"No stored workout with activity_id {activity_id}.")
        rows = stored_samples(repo, activity_id)
    finally:
        repo.close()
    stream = bucket_stream(rows, workout.get("duration_sec")) if rows else None
    if stream is None:
        raise DetailNotStored("No detailed data is stored for this workout.")
    power_zones = None
    if workout.get("discipline") == "bike":
        split = power_zone_split(stream["samples"], get_athlete_profile().get("ftp_w"), stream["bucket_sec"])
        if split.get("status") == "ok":
            power_zones = split["seconds"]
    return {
        "activity_id": activity_id,
        "discipline": workout.get("discipline"),
        "bucket_sec": stream["bucket_sec"],
        "samples": stream["samples"],
        "power_zones": power_zones,
    }
