"""
Orchestrates a manual sync: pull Garmin -> parse + split bricks -> estimate TSS
-> compute daily load -> write to InfluxDB -> auto-generate next week's plan.
Records a status snapshot for GET /sync/status.

Syncs are incremental: the first sync into an empty store (a new install, or
after a full data reset) pulls FIRST_SYNC_DAYS (60); later runs only pull since
the last successful sync (plus a small overlap). Workouts come from
Garmin's Personal API (app/garmin/personal_api.py); a workout's FIT file is
fetched, stored as samples and deleted only while it has no stored samples.
VO2max alone still comes from the garminconnect library.

Every sync also recomputes TSS/IF (and swim moving pace) for ALL stored
workouts from the current athlete profile, not just the pulled window, so
threshold edits in Settings and formula changes reach the whole history --
otherwise old points keep values from whatever the profile was at their sync.
recompute_stored_metrics() also runs standalone (no Garmin pull) for backfills.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from app.config import ROOT, get_athlete_profile
from app.garmin.client import GarminPullError, pull_vo2max
from app.garmin.personal_api import pull_activities, pull_recovery, pull_sleep, purge_fit_tmp
from app.garmin.parser import parse_activities
from app.influx.client import InfluxRepo, drop_split_brick_parents
from app.influx.queries import (
    latest_recovery_point,
    latest_sleep_point,
    oldest_workout_point,
    sampled_activity_ids,
    vo2max_last_n_days,
    workout_activity_ids_last_n_days,
    workouts_last_n_days,
)
from app.metrics.calculator import (
    apply_tss,
    compute_load,
    daily_tss,
    estimate_tss_method,
    swim_moving_seconds,
    swim_pace_sec_per_100m,
)
from app.season import service as season_service

log = logging.getLogger("jymalaya.sync")

STATUS_FILE = ROOT / "data" / "sync_status.json"
DISCIPLINES = ("swim", "bike", "run", "strength")

# Incremental window: re-pull this many days before the last successful sync to
# catch activities that reached Garmin late (a watch synced days afterwards).
# Re-pulling known activities is cheap -- Influx upserts by activity_id + time.
OVERLAP_DAYS = 3
# The full window: what the first sync into an empty store pulls, and the cap on
# an incremental one. 60 days, so the 8-week charts and the 60-day Training Days
# grid are filled from the first sync.
FIRST_SYNC_DAYS = 60
# Daily load is recomputed from everything stored, not just the pull window.
HISTORY_DAYS = 3650

# VO2max follows the incremental activity window; the panel reads its 28 days
# from what is stored. This is the span pulled when none of those days has a
# reading stored (see _vo2max_days).
VO2MAX_WINDOW_DAYS = 28


def _vo2max_days(repo: InfluxRepo, days: int) -> int:
    """
    Days of VO2max to pull: the incremental window `days` _window_days already
    chose, or the panel's full span when it would otherwise stay empty -- a
    first pull that failed while its sync succeeded leaves a last_sync behind,
    so the window alone would never go back for those days.

    Never raises; an unreadable store falls back to the incremental window.
    """
    try:
        if repo.query(vo2max_last_n_days(VO2MAX_WINDOW_DAYS)):
            return days
        return max(days, VO2MAX_WINDOW_DAYS)
    except Exception as e:  # noqa: BLE001 -- best-effort, like the pull itself
        log.warning("Could not read the VO2max window: %s", e, exc_info=True)
        return days


def _recovery_start(repo: InfluxRepo, raw: list[dict], days: int) -> date:
    """
    First day of the HRV / resting-HR pull.

    With nothing stored yet (the first sync after the feature landed, or after
    a full data reset) the pull is the backfill: back to the first stored
    activity, or the earliest one just pulled if that is older (a fresh install
    writes its first workouts later in this same sync). The UTC instant of a
    workout point can fall a day after its local calendar day, hence the extra
    day. Otherwise it is the incremental activity window -- days since the last
    sync plus the overlap -- the same `days` _window_days already chose.

    Never raises; an unreadable store falls back to the incremental window.
    """
    today = date.today()
    incremental = today - timedelta(days=max(1, days) - 1)
    try:
        if repo.query(latest_recovery_point()):
            return incremental
        candidates: list[date] = []
        for row in repo.query(oldest_workout_point())[:1]:
            t = row.get("time")
            if isinstance(t, str):
                t = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if isinstance(t, datetime):
                candidates.append(t.date() - timedelta(days=1))
        for a in raw:
            try:
                candidates.append(date.fromisoformat(str(a.get("startTimeLocal") or "")[:10]))
            except ValueError:
                continue
        return min([incremental, *candidates])
    except Exception as e:  # noqa: BLE001 -- best-effort, like the pull itself
        log.warning("Could not read the recovery window: %s", e, exc_info=True)
        return incremental

# Nights of sleep the first sleep pull reaches back: the 8-week sleep chart
# (TREND_DAYS in frontend/src/derive.ts). One request per night, so not further.
SLEEP_BACKFILL_DAYS = 56


def _sleep_start(repo: InfluxRepo, days: int) -> date:
    """
    First night of the sleep pull: SLEEP_BACKFILL_DAYS back when no sleep is
    stored (the first sync after the feature landed, or after a data reset),
    else the incremental activity window. Never raises.
    """
    today = date.today()
    incremental = today - timedelta(days=max(1, days) - 1)
    try:
        if repo.query(latest_sleep_point()):
            return incremental
    except Exception as e:  # noqa: BLE001 -- best-effort, like the pull itself
        log.warning("Could not read the sleep window: %s", e, exc_info=True)
        return incremental
    return min(incremental, today - timedelta(days=SLEEP_BACKFILL_DAYS - 1))


# Live phase of the currently running sync, readable from /sync/status while
# run_sync blocks in another request thread (same GIL-atomicity note as
# garmin.client.MFA_STATE — single-key access, no lock needed).
# None = no sync running. Keys, not display text: the frontend owns the wording.
SYNC_PHASE: dict = {"phase": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_status(status: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status, indent=2), encoding="utf-8")


def get_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {"last_sync": None, "activity_count": 0, "status": "never_run", "error": None}


def _history_blank(repo: InfluxRepo) -> bool:
    """
    True when no workout is stored at all. A full data reset wipes InfluxDB but
    not the sync status, so `last_sync` alone would make the first sync after it
    an incremental few days. An unreadable store is not blank: the window then
    follows `last_sync`, as before.
    """
    try:
        return not repo.query(oldest_workout_point())
    except Exception as e:  # noqa: BLE001 -- best-effort, never fail the sync
        log.warning("Could not read the stored history: %s", e, exc_info=True)
        return False


def _window_days(status: dict, requested: int) -> int:
    """
    Days to pull from Garmin: the full `requested` window when there is no
    successful sync on record (first run, data reset, corrupt snapshot),
    otherwise just the days since the last sync plus OVERLAP_DAYS.
    """
    last = status.get("last_sync")
    if not last:
        return requested
    try:
        last_date = datetime.fromisoformat(last).date()
    except (TypeError, ValueError):
        return requested
    since = (datetime.now(timezone.utc).date() - last_date).days
    return max(OVERLAP_DAYS, min(requested, since + OVERLAP_DAYS))


def _stored_base_ids(repo: InfluxRepo, days: int) -> set[str]:
    """
    Raw Garmin activity ids already stored for the window. Brick splits are
    stored as {parentId}_bike/_run/_leg while enrichment sees the raw parent
    activityId, so suffixes are stripped (Garmin ids are numeric). Best-effort:
    an Influx failure just means every activity gets enriched, as before.
    """
    try:
        rows = repo.query(workout_activity_ids_last_n_days(days))
    except Exception:
        log.warning("Could not read stored activity ids -- enriching everything")
        return set()
    ids = set()
    for r in rows:
        sid = str(r.get("activity_id") or r.get("_value") or "")
        if sid:
            ids.add(sid.split("_")[0])
    return ids


def _sampled_ids(repo: InfluxRepo, days: int) -> set[str]:
    """
    Stored ids (brick legs as {parent}_{suffix}) that already have FIT samples
    in the window. Best-effort: an Influx failure means every workout's FIT is
    fetched again, which rewrites the same points.
    """
    try:
        rows = repo.query(sampled_activity_ids(days))
    except Exception:
        log.warning("Could not read sampled activity ids -- fetching every FIT file")
        return set()
    return {str(r.get("activity_id") or r.get("_value")) for r in rows if r.get("activity_id") or r.get("_value")}


def _drop_known_unsplit_bricks(workouts: list[dict], known_ids: set[str]) -> list[dict]:
    """
    Drop a brick that reached the parser without leg data when that activity is
    already stored. Such a parent passes through as one entry under the raw
    Garmin id (its legs would be {id}_bike/_run) carrying the whole session's
    duration, so writing it next to legs already in InfluxDB double-counts the
    brick. Bricks are normally enriched during the pull; this only catches a
    leg fetch that came back empty.
    """
    out = []
    for w in workouts:
        aid = str(w.get("activity_id") or "")
        if w.get("is_brick_split") and "_" not in aid and aid in known_ids:
            log.warning(
                "Brick %s arrived without leg data and is already stored -- not writing "
                "the un-split parent",
                aid,
            )
            continue
        out.append(w)
    return out


def _plan_is_current() -> bool:
    """
    True when the cached plan needs no regeneration: its rolling 7-day window
    still starts today (local date, matching the anchor build_plan_payload
    uses) AND it was generated after the athlete last edited their custom
    coach instructions or training schedule, AND after the season plan last
    changed something the plan payload reads.
    Otherwise the plan is stale even with no new activities.

    The season plan is compared by its own payload_changed_at, not the file's
    mtime: the file is rewritten on every sync to store the week's actuals.
    """
    from app.instructions import INSTRUCTIONS_FILE
    from app.recommend_service import latest_recommendation
    from app.schedule_store import SCHEDULE_FILE
    from app.season import store as season_store

    plan = latest_recommendation()
    generated = ((plan or {}).get("_meta") or {}).get("generated_at") or ""
    try:
        generated_at = datetime.fromisoformat(generated)
    except ValueError:
        return False
    if generated_at.astimezone().date() != date.today():
        return False
    for edited_file in (INSTRUCTIONS_FILE, SCHEDULE_FILE):
        try:
            edited = datetime.fromtimestamp(
                edited_file.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            continue  # file never written -> nothing to invalidate the plan
        if generated_at < edited:
            return False
    season_changed = season_store.payload_changed_at()
    if season_changed:
        try:
            if generated_at < datetime.fromisoformat(season_changed):
                return False
        except ValueError:
            return False
    return True


def _stored_workout_history(repo: InfluxRepo) -> list[dict]:
    """All stored workouts, with the `date` string daily_tss expects."""
    rows = drop_split_brick_parents(repo.query(workouts_last_n_days(HISTORY_DAYS)))
    for r in rows:
        # `date` is stored with the point; only rows written before that field
        # existed need it derived, and then it must be the LOCAL date -- the
        # point's UTC date can fall on the wrong day for an evening workout.
        if r.get("date"):
            continue
        t = r.get("time")
        if isinstance(t, datetime):
            r["date"] = t.astimezone().date().isoformat()
    return rows


def _recompute_rows(rows: list[dict], profile: dict) -> list[dict]:
    """
    Re-derive swim pace and TSS/IF on stored workout rows in place, from the
    current profile. Returns only the rows whose values changed (the ones that
    need rewriting). A row whose new TSS would be None keeps its stored value:
    Influx merges fields on rewrite, so the stale number would survive anyway
    and the daily series should keep using it.

    Swim pace is re-derived from the row's moving time; rows stored before
    moving time was captured fall back to their timer duration, which is what
    they were already computed from.
    """
    changed = []
    for r in rows:
        before = (
            r.get("avg_pace_sec_per_100m"), r.get("tss"), r.get("intensity_factor"), r.get("tss_method")
        )
        if r.get("discipline") == "swim":
            pace = swim_pace_sec_per_100m(swim_moving_seconds(r), r.get("distance_m"))
            if pace is not None:
                r["avg_pace_sec_per_100m"] = pace
        tss, intensity, method = estimate_tss_method(r, profile)
        if tss is not None:
            r["tss"], r["intensity_factor"], r["tss_method"] = tss, intensity, method
        after = (
            r.get("avg_pace_sec_per_100m"), r.get("tss"), r.get("intensity_factor"), r.get("tss_method")
        )
        if after != before:
            changed.append(r)
    return changed


def _restore_point_time(row: dict) -> dict:
    """
    Give a stored row the start field write_workouts needs to land back on its
    own Influx point (same instant = overwritten in place, not duplicated).

    It has to go back as `start_time_utc`: `start_time` is local wall clock and
    would be converted again on write, drifting the point by the UTC offset on
    every recompute.
    """
    t = row.get("time")
    if isinstance(t, datetime) and not row.get("start_time_utc"):
        row["start_time_utc"] = t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return row


def recompute_stored_metrics(
    repo: InfluxRepo, profile: dict | None = None, days: int | None = None
) -> dict:
    """
    Recompute stored workouts' metrics from the current athlete profile,
    rewrite the changed points, and rebuild the daily CTL/ATL/TSB series from
    the corrected history. Runs inside every sync (all workouts); also
    callable standalone (e.g. after a threshold change) without touching
    Garmin. `days` limits which workouts are recomputed (the Settings
    "Re-calculate" button uses 7); the daily series is always rebuilt from
    the full history, since CTL/ATL are cumulative.
    """
    profile = profile or get_athlete_profile()
    history = _stored_workout_history(repo)
    candidates = history
    if days is not None:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        candidates = [r for r in history if (r.get("date") or "") >= cutoff]
    changed = _recompute_rows(candidates, profile)
    for r in changed:
        _restore_point_time(r)
    rewritten = repo.write_workouts(changed) if changed else 0

    # CTL/ATL are cumulative EWAs, so the daily series must be recomputed from
    # the full stored history -- an incremental window alone would restart
    # them near zero. Combined plus per-discipline series.
    #
    # `through=today` carries every series past its last workout, decaying the
    # trailing rest days (see compute_load). Rebuilding from the full history
    # also means this one call repairs stored history retroactively: Influx
    # keys a daily_load point on measurement + discipline + timestamp, so each
    # day is overwritten in place and the series only ever grows.
    today = date.today()
    load_rows = compute_load(daily_tss(history), discipline="all", through=today)
    for disc in DISCIPLINES:
        load_rows += compute_load(daily_tss(history, disc), discipline=disc, through=today)
    repo.write_daily_load(load_rows)
    return {
        "stored_workouts": len(history),
        "considered": len(candidates),
        "rewritten": rewritten,
        "load_days": len(load_rows),
    }


def run_sync(days: int = FIRST_SYNC_DAYS, keep_requests: bool = True) -> dict:
    """
    Run a full sync. Returns a summary dict. Garmin/auth failures are caught and
    recorded in the status snapshot, then re-raised as GarminPullError for the
    router to surface as a clean HTTP error.

    `keep_requests` is passed to the plan regeneration (see run_recommendation).
    False is the athlete's explicit "overwrite with a new plan", so it always
    regenerates, even when the sync would otherwise skip the plan.
    """
    try:
        return _run_sync(days, keep_requests)
    finally:
        SYNC_PHASE["phase"] = None


def _run_sync(days: int, keep_requests: bool = True) -> dict:
    profile = get_athlete_profile()
    prev = get_status()

    repo = InfluxRepo()
    try:
        # An empty store pulls the full window whatever last_sync says.
        days = days if _history_blank(repo) else _window_days(prev, days)
        known_ids = _stored_base_ids(repo, days)
        sampled = _sampled_ids(repo, days)
        purge_fit_tmp()

        SYNC_PHASE["phase"] = "garmin"
        try:
            # Workouts, their FIT samples (for workouts without stored samples),
            # HR zones and the avatar come from the Personal API; a PersonalApiError
            # is a GarminPullError carrying the action the dashboard shows.
            raw = pull_activities(
                days=days, with_hr_zones=True, known_ids=known_ids, sampled_ids=sampled
            )
        except GarminPullError as e:
            _save_status(
                {
                    # Keep the last successful sync (and the incremental window
                    # it anchors) intact across a failed attempt.
                    "last_sync": prev.get("last_sync"),
                    "last_attempt": _now_iso(),
                    "activity_count": prev.get("activity_count", 0),
                    "status": "error",
                    "error": str(e),
                    "error_code": getattr(e, "code", None),
                }
            )
            raise

        # Deliberately no phase key of its own: the frontend owns the phase
        # display text and tests/test_sync_phase.py pins the exact phase list,
        # so a new key would cost a wording change and a test change for a step
        # that takes one request. Best-effort in the strongest sense -- cycling
        # VO2max needs a power meter and may never exist for this athlete, and
        # a max-metrics failure must not fail a sync that pulled activities.
        try:
            vo2max_rows = pull_vo2max(days=_vo2max_days(repo, days))
        except Exception as e:  # noqa: BLE001 -- never let it break the sync
            vo2max_rows = []
            log.warning("VO2max pull failed: %s", e, exc_info=True)

        # HRV and resting HR: the same best-effort contract and no phase key,
        # for the same reasons. The first pull reaches back to the first stored
        # activity, so it can be several requests; later ones are one.
        try:
            recovery_rows = pull_recovery(_recovery_start(repo, raw, days))
        except Exception as e:  # noqa: BLE001 -- never let it break the sync
            recovery_rows = []
            log.warning("Recovery (HRV / resting HR) pull failed: %s", e, exc_info=True)
        # Nightly sleep, same contract. Stored in the same `recovery` points
        # (write_recovery writes only the fields a row has, so a sleep row
        # merges with that day's HRV and resting HR).
        try:
            recovery_rows = recovery_rows + pull_sleep(_sleep_start(repo, days))
        except Exception as e:  # noqa: BLE001 -- never let it break the sync
            log.warning("Sleep pull failed: %s", e, exc_info=True)

        SYNC_PHASE["phase"] = "parsing"
        workouts = apply_tss(parse_activities(raw), profile)
        workouts = _drop_known_unsplit_bricks(workouts, known_ids)

        SYNC_PHASE["phase"] = "metrics"
        # No athlete_profile write here: the mirror is written only when the
        # athlete changes a value in Settings (routers/athlete.py), so each
        # point in it marks a real threshold change rather than a sync.
        written = repo.write_workouts(workouts)
        # The FIT samples, laps and planned workouts. Best-effort: a failure
        # leaves those workouts without samples, so the next sync fetches them
        # again; their TSS falls back to average pace meanwhile.
        try:
            samples_written = repo.write_workout_detail(workouts)
        except Exception as e:  # noqa: BLE001 -- never let it break the sync
            samples_written = 0
            log.warning("Workout sample write failed: %s", e, exc_info=True)
        try:
            vo2max_written = repo.write_vo2max(vo2max_rows)
        except Exception as e:  # noqa: BLE001 -- same contract as the pull
            vo2max_written = 0
            log.warning("VO2max write failed: %s", e, exc_info=True)
        try:
            recovery_written = repo.write_recovery(recovery_rows)
        except Exception as e:  # noqa: BLE001 -- same contract as the pull
            recovery_written = 0
            log.warning("Recovery write failed: %s", e, exc_info=True)

        # Recompute the whole stored history (TSS/IF, swim pace, daily
        # CTL/ATL/TSB) from the current profile -- see the module docstring.
        recompute_stored_metrics(repo, profile)

        # The season plan catches up with the refreshed load: actuals, form and
        # the adaptation rules. On every sync, including one with nothing new --
        # TSB moves on rest days. No phase key of its own (the phase list is
        # pinned) and best effort: a season-plan failure must not fail a sync
        # that pulled activities fine. With no plan it reads nothing.
        try:
            season_notice = season_service.on_sync(repo)
        except Exception as e:  # noqa: BLE001 -- never let it break the sync
            season_notice = None
            log.warning("Season plan update failed: %s", e, exc_info=True)
    finally:
        repo.close()

    bricks = sum(1 for w in workouts if w.get("is_brick_split"))
    gaps = sum(1 for w in workouts if w.get("tss") is None)
    summary = {
        "last_sync": _now_iso(),
        "last_attempt": _now_iso(),
        "activity_count": len(raw),
        "new_activities": sum(
            1 for a in raw if str(a.get("activityId")) not in known_ids
        ),
        "window_days": days,
        "workout_entries": len(workouts),
        "points_written": written,
        "samples_written": samples_written,
        "vo2max_points": vo2max_written,
        "recovery_points": recovery_written,
        "brick_entries": bricks,
        "tss_gaps": gaps,
        "status": "ok",
        "error": None,
        # "Plan adjusted: ..." when the sync moved a season-plan target.
        "season_plan_notice": season_notice,
    }

    # Auto-generate next week's plan (PRD 7.5), unless there is nothing new to
    # plan around: no new activities AND a current cached plan (window starts
    # today, coach instructions unchanged since generation). That makes a
    # repeat sync skip the slow, paid Claude call -- except when the athlete
    # asked to overwrite their plan changes, which is a request for a new plan.
    # Best-effort: a Claude failure (or missing API key) must never fail the
    # sync itself.
    if keep_requests and summary["new_activities"] == 0 and _plan_is_current():
        summary["recommendation"] = "skipped (no new activities)"
        log.info("Plan regeneration skipped: no new activities, plan is current")
    else:
        SYNC_PHASE["phase"] = "coach"
        try:
            from app.recommend_service import run_recommendation

            plan = run_recommendation(trigger="auto_sync", keep_requests=keep_requests)
            summary["recommendation"] = "generated"
            # Six flat or only-rising weeks made this plan lighter (rule 32):
            # the dashboard shows the sentence in its notice strip.
            reduction = (plan.get("load_reduction") if isinstance(plan, dict) else None) or {}
            summary["load_reduction_notice"] = reduction.get("message")
        except Exception as e:  # noqa: BLE001 -- never let it break the sync
            summary["recommendation"] = f"skipped ({type(e).__name__})"
            # The sync must survive, but the failure must not be invisible.
            log.warning("Auto-recommendation after sync failed: %s", e, exc_info=True)

    _save_status(summary)
    return summary
