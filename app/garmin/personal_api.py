"""
Garmin's official Personal API: workouts, their FIT files, HRV, resting HR,
nightly sleep and the avatar. VO2max has no endpoint here and stays on the unofficial library
(app/garmin/client.py), which is now its only use.

Auth is an API key the athlete creates on Garmin Connect's "Personal API Key"
page, kept in .env as GARMIN_PERSONAL_API_KEY. A POST exchanges it for a 24-hour
access token (plain text; no refresh token, so a new one is simply requested).
No password, no MFA, no bot detection. Keys expire (30 days to a year) and are
renewed by hand: that failure surfaces as PersonalApiError("key_rejected") with
the action the dashboard shows the athlete.

Never logs the key or the token. Confirmed live 2026-10-23 and 2026-11-06; the
response fields are listed in assets/garmin_parameters.md.

Things easy to re-break:
- **Brick legs come inline.** With excludeChildren=false the list carries each
  multisport child as its own row with `parentId`. pull_activities folds them
  into the parent's `_legs` and removes them from the top level -- left there
  they would be stored a second time as standalone workouts.
- **The FIT file is deleted right after decoding** (in a finally), and
  data/fit_tmp is purged at startup. Only the 5 s samples and laps survive, in
  InfluxDB, without coordinates (fit.py).
- **Spans are chunked at CHUNK_DAYS**: a 365-day request answered HTTP 500 on
  the activity list and the daily summaries; 120 worked.
- **A FIT failure never fails a sync**: that workout keeps average-pace TSS and
  is picked up again by the next sync, since it still has no stored samples.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.config import ROOT
from app.garmin import fit
from app.garmin.client import (
    AVATAR_DIR,
    AVATAR_MAX_AGE_DAYS,
    AVATAR_STEM,
    GarminPullError,
    avatar_file,
    parse_hrv,
    zones_plausible,
)
from app.garmin.parser import _discipline, _is_multisport, leg_activity_id
from app.metrics.ngp import ngp_sec_per_km

log = logging.getLogger("jymalaya.garmin.personal")

BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
KEY_ENV = "GARMIN_PERSONAL_API_KEY"
# The token lasts 24 hours; renew a little early rather than find out mid-sync.
TOKEN_TTL_SEC = 23 * 3600
TIMEOUT = 60
CHUNK_DAYS = 90
PAGE_SIZE = 200
FIT_TMP = ROOT / "data" / "fit_tmp"
_ZONE_FIELDS = tuple(f"hrTimeInZone_{i}" for i in range(1, 6))
# Garmin's HR-zone sport names for each discipline; DEFAULT covers the rest.
_ZONE_SPORT = {"run": "RUNNING", "bike": "CYCLING", "swim": "SWIMMING"}
# Physiological bounds for resting HR (same as the library path).
_RHR_BOUNDS = (25.0, 120.0)
# A night's sleep outside these bounds (seconds) is a watch artefact, not sleep.
_SLEEP_BOUNDS = (3600, 16 * 3600)
# Sleep has no working range endpoint (confirmed 2026-11-13: the sleep-service
# stats range answers null), so one request per night, a few at a time.
SLEEP_WORKERS = 4

# Suggested actions, shown by the dashboard's Garmin error modal.
ACTION_KEY = (
    "Create a new key on the Personal API Key page in Garmin Connect "
    "(Account settings) and paste it here."
)
ACTION_RETRY = "Garmin did not answer. Try again in a few minutes."


class PersonalApiError(GarminPullError):
    """
    A Personal API failure with a code the dashboard maps to an action.
    Subclasses GarminPullError so the sync's status handling and the router's
    502 keep working unchanged.

    codes: no_key, key_rejected, garmin_error, network
    """

    def __init__(self, code: str, message: str, action: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, "action": self.action}


_TOKEN: dict = {"value": None, "at": 0.0}


def reset_token() -> None:
    """Forget the cached access token (after the key changes)."""
    _TOKEN["value"] = None
    _TOKEN["at"] = 0.0


def has_key() -> bool:
    return bool(os.getenv(KEY_ENV))


def _key() -> str:
    key = os.getenv(KEY_ENV)
    if not key:
        raise PersonalApiError(
            "no_key", "No Garmin Personal API key is set.", ACTION_KEY
        )
    return key


def _token(force: bool = False) -> str:
    if not force and _TOKEN["value"] and time.time() - _TOKEN["at"] < TOKEN_TTL_SEC:
        return _TOKEN["value"]
    token = exchange_key(_key())
    _TOKEN["value"], _TOKEN["at"] = token, time.time()
    log.debug("Personal API token obtained")
    return token


def exchange_key(key: str) -> str:
    """Trade an API key for an access token, or raise PersonalApiError."""
    try:
        resp = httpx.post(
            f"{BASE}{TOKEN_PATH}",
            headers={"Authorization": f"ApiKey {key}"},
            timeout=TIMEOUT,
        )
    except httpx.TransportError as e:
        raise PersonalApiError(
            "network", f"Could not reach Garmin ({type(e).__name__}).", ACTION_RETRY
        ) from e
    if resp.status_code in (400, 401, 403):
        raise PersonalApiError(
            "key_rejected",
            "Garmin rejected the Personal API key: it has expired or was revoked.",
            ACTION_KEY,
        )
    if not resp.is_success:
        raise PersonalApiError(
            "garmin_error", f"Garmin answered HTTP {resp.status_code} to the key exchange.", ACTION_RETRY
        )
    token = _parse_token(resp)
    if not token:
        raise PersonalApiError(
            "garmin_error", "Garmin's key exchange returned no token.", ACTION_RETRY
        )
    return token


def _parse_token(resp: httpx.Response) -> str | None:
    """The token is plain text (confirmed live); JSON variants are accepted too."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip() or None
    if isinstance(body, str):
        return body.strip() or None
    if isinstance(body, dict):
        for k in ("access_token", "accessToken", "token", "personalApiToken"):
            if body.get(k):
                return str(body[k])
    return None


def _request(path: str, params: dict | None = None, *, raw: bool = False) -> httpx.Response | None:
    """
    GET a Personal API path. Re-requests the token once on a 401. Returns None
    on 404 (nothing there, e.g. no structured workout). Raises PersonalApiError
    on anything else that is not a success.
    """
    # A file download answers 406 to Accept: application/json.
    accept = "*/*" if raw else "application/json"
    for attempt in (0, 1):
        try:
            resp = httpx.get(
                f"{BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {_token(force=attempt == 1)}", "Accept": accept},
                timeout=TIMEOUT,
            )
        except httpx.TransportError as e:
            raise PersonalApiError(
                "network", f"Could not reach Garmin ({type(e).__name__}).", ACTION_RETRY
            ) from e
        if resp.status_code == 401 and attempt == 0:
            continue
        break
    if resp.status_code == 404:
        return None
    if resp.status_code in (401, 403):
        raise PersonalApiError(
            "key_rejected", "Garmin refused the Personal API token.", ACTION_KEY
        )
    if not resp.is_success:
        raise PersonalApiError(
            "garmin_error", f"Garmin answered HTTP {resp.status_code} for {path.split('/')[1]}.", ACTION_RETRY
        )
    return resp


def _json(path: str, params: dict | None = None):
    resp = _request(path, params)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _spans(start: date, end: date, chunk: int = CHUNK_DAYS):
    s = start
    while s <= end:
        e = min(end, s + timedelta(days=chunk - 1))
        yield s, e
        s = e + timedelta(days=1)


# -- activities -------------------------------------------------------------
def list_activities(start: date, end: date) -> list[dict]:
    """Every activity (children included) from start to end, deduplicated by id."""
    seen: dict[str, dict] = {}
    for a, b in _spans(start, end):
        offset = 0
        while True:
            page = _json(
                "/activitylist-service/activities/search/activities",
                {
                    "startDate": a.isoformat(),
                    "endDate": b.isoformat(),
                    "start": offset,
                    "limit": PAGE_SIZE,
                    "excludeChildren": "false",
                },
            )
            rows = page if isinstance(page, list) else []
            for r in rows:
                if isinstance(r, dict) and r.get("activityId") is not None:
                    seen[str(r["activityId"])] = r
            if len(rows) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    return list(seen.values())


def get_activity(activity_id) -> dict:
    return _json(f"/activity-service/activity/{activity_id}") or {}


def planned_workout(activity_id) -> dict | None:
    """
    The structured workout the activity was run against, flattened to
    {"name", "steps": [...]}, or None for a plain "press start" session (the
    common case, which the analysis reads as "no structure to compare").
    """
    try:
        raw = _json(f"/activity-service/activity/{activity_id}/workouts")
    except PersonalApiError:
        log.warning("Planned workout fetch failed for %s", activity_id, exc_info=True)
        return None
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    workout = next((w for w in items if isinstance(w, dict)), None)
    if not workout:
        return None
    steps = []
    for s in workout.get("steps") or []:
        if not isinstance(s, dict):
            continue
        flat = {
            "step": (s.get("intensity") or s.get("durationType") or "").lower() or None,
            "duration_type": s.get("durationType"),
            "duration_value": s.get("durationValue"),
            "target_type": s.get("targetType"),
            "target_low": s.get("targetValueLow"),
            "target_high": s.get("targetValueHigh"),
            "repeat": s.get("targetValue") if (s.get("durationType") or "").startswith("REPEAT") else None,
            "description": s.get("notes"),
        }
        steps.append({k: v for k, v in flat.items() if v not in (None, "")})
    if not steps:
        return None
    return {"name": workout.get("workoutName"), "steps": steps}


def download_fit(activity_id, dest_dir: Path = FIT_TMP) -> Path | None:
    """The activity's original FIT file, written under dest_dir; None if absent."""
    resp = _request(f"/download-service/files/activity/{activity_id}", raw=True)
    if resp is None:
        return None
    data = resp.content
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".fit")), None)
            if not name:
                return None
            data = z.read(name)
    if data[8:12] != b".FIT":
        log.warning("Activity %s download is not a FIT file", activity_id)
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{activity_id}.fit"
    path.write_bytes(data)
    return path


def purge_fit_tmp(dest_dir: Path = FIT_TMP) -> int:
    """Delete any FIT file left behind (a crash between download and decode)."""
    removed = 0
    if dest_dir.is_dir():
        for f in dest_dir.glob("*.fit"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _zone_floors(cache: dict) -> dict[str, list[float]]:
    """Garmin's HR-zone floors per sport, fetched once per pull."""
    if "floors" in cache:
        return cache["floors"]
    floors: dict[str, list[float]] = {}
    try:
        raw = _json("/biometric-service/heartRateZones/v2") or []
        for item in raw if isinstance(raw, list) else [raw]:
            vals = [item.get(f"zone{i}Floor") for i in range(1, 6)]
            if item.get("sport") and all(isinstance(v, (int, float)) for v in vals):
                floors[str(item["sport"]).upper()] = [float(v) for v in vals]
    except PersonalApiError:
        log.warning("Could not read the HR zone floors", exc_info=True)
    cache["floors"] = floors
    return floors


def _list_zones(activity: dict) -> dict | None:
    """Time in HR zones straight off the list row (seconds), or None."""
    zones = {
        f"Z{i}": float(activity[f])
        for i, f in enumerate(_ZONE_FIELDS, start=1)
        if isinstance(activity.get(f), (int, float))
    }
    return zones or None


def enrich_with_fit(activity: dict, discipline: str, cache: dict) -> bool:
    """
    Download, decode and delete one activity's FIT file, attaching `_detail`
    (samples, laps, planned workout), `_rpe` / `_feel` when the athlete rated
    the session on the watch, `_ngp_sec_per_km` for runs and, when the
    list's zone numbers are implausible, `_hr_zones` recomputed from the HR
    records. Returns True when samples were attached. Never raises.
    """
    aid = activity.get("activityId")
    path = None
    try:
        path = download_fit(aid)
        if path is None:
            return False
        records, laps, rating = fit.decode(path)
    except Exception:
        log.warning("FIT download/decode failed for %s", aid, exc_info=True)
        return False
    finally:
        if path is not None:
            try:
                path.unlink()
            except OSError:
                log.warning("Could not delete %s", path)

    samples = fit.to_samples(records, discipline)
    if not samples:
        return False
    activity["_detail"] = {
        "samples": samples,
        "laps": fit.to_laps(laps, discipline),
        "planned": planned_workout(aid),
    }
    for key in ("rpe", "feel"):
        if rating.get(key) is not None:
            activity[f"_{key}"] = rating[key]
    if discipline == "run":
        activity["_ngp_sec_per_km"] = ngp_sec_per_km(samples)
    if not zones_plausible(activity.get("_hr_zones"), activity.get("duration")):
        floors = _zone_floors(cache)
        sport_floors = floors.get(_ZONE_SPORT.get(discipline, ""), floors.get("DEFAULT"))
        repaired = fit.hr_zones_from_records(records, sport_floors or [])
        if repaired:
            activity["_hr_zones"] = repaired
    return True


def refresh_avatar_from(activities: list[dict]) -> None:
    """Cache the profile image from the URL each activity row carries. Never raises."""
    try:
        existing = avatar_file()
        if existing and time.time() - existing.stat().st_mtime < AVATAR_MAX_AGE_DAYS * 86400:
            return
        url = next(
            (
                a.get(k)
                for a in activities
                for k in ("ownerProfileImageUrlLarge", "ownerProfileImageUrlMedium", "ownerProfileImageUrlSmall")
                if a.get(k)
            ),
            None,
        )
        if not url:
            return
        resp = httpx.get(url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        ext = next((f".{t}" for t in ("png", "gif", "webp") if t in ctype), ".jpg")
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        if existing and existing.suffix != ext:
            existing.unlink()
        (AVATAR_DIR / f"{AVATAR_STEM}{ext}").write_bytes(resp.content)
    except Exception:
        log.debug("Could not fetch the Garmin profile image", exc_info=True)


def _fold_children(activities: list[dict]) -> list[dict]:
    """
    Attach each multisport child to its parent as `_legs` (transitions dropped,
    oldest first) and return only top-level activities. A child whose parent is
    not in the window is dropped: its parent was stored with it earlier.
    """
    by_id = {str(a.get("activityId")): a for a in activities}
    children: dict[str, list[dict]] = {}
    top = []
    for a in activities:
        pid = a.get("parentId")
        if pid is not None:
            children.setdefault(str(pid), []).append(a)
        else:
            top.append(a)
    for pid, kids in children.items():
        parent = by_id.get(pid)
        if parent is None:
            log.debug("Dropping %d brick legs whose parent %s is outside the window", len(kids), pid)
            continue
        legs = [
            k for k in kids
            if "transition" not in ((k.get("activityType") or {}).get("typeKey") or "").lower()
        ]
        legs.sort(key=lambda k: k.get("startTimeLocal") or "")
        if legs:
            parent["_legs"] = legs
    return top


def pull_activities(
    days: int = 28,
    *,
    with_hr_zones: bool = True,
    known_ids: set[str] | None = None,
    sampled_ids: set[str] | None = None,
) -> list[dict]:
    """
    The last `days` days of activities as list-shaped dicts (oldest first), the
    shape app/garmin/parser.py reads. Brick legs are folded into `_legs`.

    HR zones come off the list rows for free. Every workout without stored
    samples (`sampled_ids` holds the stored ids, legs as {parent}_{suffix}) gets
    its FIT file: samples, laps, planned workout and, for runs, NGP. `known_ids`
    is accepted for the old signature and unused: an activity is "done" here
    only once its samples are stored.

    Raises PersonalApiError (a GarminPullError) when the list itself cannot be
    read; a single FIT failure is logged and skipped.
    """
    del known_ids  # the samples, not the workout point, decide what is fetched
    end = date.today()
    start = end - timedelta(days=max(1, days) - 1)
    log.debug("<<< Personal API: activities %s..%s (%d days)", start, end, days)
    activities = _fold_children(list_activities(start, end))
    refresh_avatar_from(activities)

    sampled_ids = sampled_ids or set()
    cache: dict = {}
    fetched = 0
    units: list[tuple[dict, str, str]] = []  # (activity, stored id, discipline)
    for a in activities:
        if with_hr_zones:
            for leg in a.get("_legs") or []:
                z = _list_zones(leg)
                if z:
                    leg["_hr_zones"] = z
            z = _list_zones(a)
            if z and not a.get("_legs"):
                a["_hr_zones"] = z
        if _is_multisport(a):
            for leg in a.get("_legs") or []:
                disc = _discipline((leg.get("activityType") or {}).get("typeKey"))
                units.append((leg, leg_activity_id(a.get("activityId"), leg), disc))
        else:
            disc = _discipline((a.get("activityType") or {}).get("typeKey"))
            units.append((a, str(a.get("activityId")), disc))

    for unit, stored_id, disc in units:
        if stored_id in sampled_ids:
            continue
        if enrich_with_fit(unit, disc, cache):
            fetched += 1

    activities.sort(key=lambda a: a.get("startTimeLocal") or a.get("startTimeGMT") or "")
    log.info(
        "Personal API: %d activities, FIT samples fetched for %d of %d workouts",
        len(activities), fetched, len(units),
    )
    return activities


# -- recovery ---------------------------------------------------------------
def parse_daily_rhr(payload) -> list[dict]:
    """[{"date", "resting_hr"}] from /usersummary-service/v2/dailysummaries."""
    rows = payload.get("data") if isinstance(payload, dict) else payload
    out: dict[str, dict] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        day = str(r.get("calendarDate") or "")[:10]
        value = (r.get("heartRate") or {}).get("restingValue")
        try:
            date.fromisoformat(day)
            v = float(value)
        except (TypeError, ValueError):
            continue
        if _RHR_BOUNDS[0] <= v <= _RHR_BOUNDS[1]:
            out[day] = {"date": day, "resting_hr": round(v, 1)}
    return sorted(out.values(), key=lambda r: r["date"])


def pull_recovery(start: date, end: date | None = None) -> list[dict]:
    """
    Daily HRV + resting HR over start..end, merged per day as
    [{"date", "hrv", "resting_hr"}] oldest first -- the shape write_recovery
    takes. Best-effort, the old contract: a span that fails is skipped, and a
    missing key returns [] (the sync already reported it on the activity pull).
    """
    end = end or date.today()
    if start > end or not has_key():
        return []
    by_day: dict[str, dict] = {}

    def put(day: str, field: str, value) -> None:
        by_day.setdefault(day, {"date": day, "hrv": None, "resting_hr": None})[field] = value

    for a, b in _spans(start, end):
        try:
            for r in parse_daily_rhr(
                _json("/usersummary-service/v2/dailysummaries",
                      {"startDate": a.isoformat(), "endDate": b.isoformat()})
            ):
                put(r["date"], "resting_hr", r["resting_hr"])
        except PersonalApiError:
            log.warning("Could not fetch resting HR for %s..%s", a, b, exc_info=True)
        try:
            for r in parse_hrv(_json(f"/hrv-service/hrv/daily/{a.isoformat()}/{b.isoformat()}")):
                put(r["date"], "hrv", r["hrv"])
        except PersonalApiError:
            log.warning("Could not fetch HRV for %s..%s", a, b, exc_info=True)
    rows = sorted(by_day.values(), key=lambda r: r["date"])
    if log.isEnabledFor(logging.DEBUG):
        log.debug("Recovery parsed: %d days\n%s", len(rows), json.dumps(rows[-7:]))
    return rows


# -- sleep ------------------------------------------------------------------
def parse_sleep(payload) -> dict | None:
    """
    {"date", "sleep_sec"} from /wellness-service/wellness/dailySleep, or None.

    `sleepTimeSeconds` is the night's sleep with naps excluded (they are
    `napTimeSeconds`). Garmin dates a night by the morning the athlete woke.
    """
    if not isinstance(payload, dict):
        return None
    day = str(payload.get("calendarDate") or "")[:10]
    try:
        date.fromisoformat(day)
        v = float(payload.get("sleepTimeSeconds"))
    except (TypeError, ValueError):
        return None
    if not _SLEEP_BOUNDS[0] <= v <= _SLEEP_BOUNDS[1]:
        return None
    return {"date": day, "sleep_sec": round(v)}


def _sleep_night(day: date) -> dict | None:
    try:
        return parse_sleep(_json("/wellness-service/wellness/dailySleep", {"date": day.isoformat()}))
    except PersonalApiError:
        log.warning("Could not fetch sleep for %s", day, exc_info=True)
        return None


def pull_sleep(start: date, end: date | None = None) -> list[dict]:
    """
    Nightly sleep over start..end as [{"date", "sleep_sec"}] oldest first.
    Best-effort like pull_recovery: a night that fails is skipped, no key
    returns [].
    """
    end = end or date.today()
    if start > end or not has_key():
        return []
    try:
        _token()  # fetch the token once, before the threads share it
    except PersonalApiError:
        log.warning("Could not fetch sleep: no Personal API token", exc_info=True)
        return []
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    with ThreadPoolExecutor(max_workers=SLEEP_WORKERS) as pool:
        rows = [r for r in pool.map(_sleep_night, days) if r]
    by_day = {r["date"]: r for r in rows}
    out = sorted(by_day.values(), key=lambda r: r["date"])
    log.info("Sleep: %d of %d nights", len(out), len(days))
    return out
