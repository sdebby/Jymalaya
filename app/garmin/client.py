"""
Garmin Connect library client: since 2026-11-06 used for VO2max only.

Workouts, FIT samples, HRV, resting HR and the avatar come from Garmin's
official Personal API (app/garmin/personal_api.py), which has no VO2max
endpoint. VO2max therefore still needs this unofficial login, its token cache
and MFA. Shared helpers (avatar paths, zones_plausible, parse_hrv) stay here.

Auth: reads GARMIN_EMAIL / GARMIN_PASSWORD from the environment (.env). Library:
garminconnect (unofficial, reverse-engineered -- not an official API). Login flows
can break or hit MFA/bot-detection without warning; we surface a clean
GarminPullError rather than a stack trace.

To reduce repeated logins (which is what trips Garmin's bot-detection), a
successful session is cached as OAuth tokens under ~/.tricoach/garmin_tokens.
Subsequent runs resume from those tokens until they expire, only falling back to a
full email/password login when needed.

(Ported from the original BrickPlan src/garmin_pull.py; auth/MFA/token logic
unchanged, fetch widened to a 28-day rolling window per the PRD.)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from app.config import ROOT

# DEBUG level dumps the raw Garmin API output to the backend terminal (handler +
# level configured in main.py). Never logs credentials, tokens, or MFA codes.
log = logging.getLogger("jymalaya.garmin")

# Resumable OAuth tokens (not credentials, but still grant Garmin access -- keep
# out of the repo via the ~/.tricoach home location).
TOKEN_STORE = Path.home() / ".tricoach" / "garmin_tokens"

# File-based MFA handoff. Garmin emails an MFA code only *after* the password is
# submitted, and the resulting login session can't be serialized across processes
# -- so the code must reach this same running process. When stdin isn't a real
# terminal (e.g. launched by uvicorn), _prompt_mfa polls this file instead of
# calling input().
MFA_CODE_FILE = Path.home() / ".tricoach" / "mfa_code"
MFA_WAIT_SECONDS = 300
# How often _prompt_mfa checks for the code file. Short, because the login
# resumes only on the next check after the athlete submits the code.
MFA_POLL_SECONDS = 0.5

# Live MFA status, readable from other request threads (GET /sync/status) while a
# sync is blocked inside _prompt_mfa. Single-key reads/writes are GIL-atomic, so
# no lock is needed.
MFA_STATE: dict = {"pending": False, "requested_at": None}


# Garmin's daily max-metrics endpoint -- the source of the VO2max the watch
# shows. The garminconnect wrapper (get_max_metrics) hardcodes start == end, so
# it costs one request per day: 365 of them for a year of backfill. The URL
# itself is a RANGE endpoint, so connectapi fetches a whole span in one request.
# This is the only place in the repo that calls connectapi directly rather than
# a named library method, which is exactly why parse_max_metrics below is
# defensive: no library type sits in front of this response.
VO2MAX_URL = "/metrics-service/metrics/maxmet/daily"
# Garmin's cap on the span is undocumented. Chunking keeps a 365-day backfill
# from being the request that finds out; 28 matches the dashboard panel's own
# window, so a sync is always exactly one request.
VO2MAX_CHUNK_DAYS = 28

# Garmin's blocks mapped to the repo's lowercase sport vocabulary. "generic" is
# the running VO2max (the number the watch calls "VO2 Max"); "cycling" exists
# only for an athlete riding with a power meter, so its absence is normal.
_VO2MAX_BLOCKS = (("generic", "run"), ("cycling", "bike"))
# The precise value when Garmin sends it, the rounded one otherwise.
_VO2MAX_KEYS = ("vo2MaxPreciseValue", "vo2MaxValue")

# HRV bounds for parse_hrv. Garmin sends 0 or null for a night it has no
# reading for, and a zero charted as a point would crush the real series. The
# HRV endpoint is the same on the Personal API, which now makes the request
# (app/garmin/personal_api.py); the parser is shared.
_HRV_BOUNDS = (5.0, 250.0)


class GarminPullError(RuntimeError):
    """Raised with a user-facing message when the Garmin pull cannot complete."""


def _prompt_mfa() -> str:
    """
    Obtain the Garmin MFA code.

    Default behaviour is the file handoff: poll MFA_CODE_FILE until the code is
    written there, then consume it. This lets a separate request hand the code to
    this still-running login session without interactive stdin (isatty() is
    unreliable on Windows background processes, so we don't depend on it).

    Set GARMIN_MFA_INTERACTIVE=1 to prompt on stdin instead, for a real terminal.
    """
    import time

    if os.getenv("GARMIN_MFA_INTERACTIVE") == "1":
        return input("Garmin MFA code (check your email/authenticator): ").strip()

    MFA_STATE["requested_at"] = datetime.now(timezone.utc).isoformat()
    MFA_STATE["pending"] = True
    try:
        MFA_CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if MFA_CODE_FILE.exists():
            MFA_CODE_FILE.unlink()  # discard any stale code from a previous attempt

        print(
            "[garmin] MFA required -- Garmin has emailed a code. Waiting for it to "
            f"be written to: {MFA_CODE_FILE} (or entered in the dashboard)",
            flush=True,
        )

        deadline = time.time() + MFA_WAIT_SECONDS
        while time.time() < deadline:
            if MFA_CODE_FILE.exists():
                code = MFA_CODE_FILE.read_text(encoding="utf-8").strip()
                if code:
                    MFA_CODE_FILE.unlink()
                    print("[garmin] MFA code received, completing login...", flush=True)
                    return code
            time.sleep(MFA_POLL_SECONDS)

        raise GarminPullError(
            f"Timed out after {MFA_WAIT_SECONDS}s waiting for an MFA code at "
            f"{MFA_CODE_FILE}."
        )
    finally:
        MFA_STATE["pending"] = False


def _connect() -> Garmin:
    """
    Establish an authenticated Garmin client, resuming from cached tokens when
    possible and falling back to a full credential login otherwise.
    """
    # Try resuming a cached session first -- no credentials needed, no bot risk.
    if TOKEN_STORE.exists():
        try:
            client = Garmin()
            client.login(str(TOKEN_STORE))
            log.debug("Garmin session resumed from cached tokens (%s)", TOKEN_STORE)
            return client
        except Exception:
            # Tokens expired/invalid -- fall through to a fresh credential login.
            log.debug("Cached Garmin tokens rejected -- falling back to credential login")

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise GarminPullError(
            "Garmin credentials missing. Set GARMIN_EMAIL and GARMIN_PASSWORD in "
            "your local .env file (see .env.example)."
        )

    try:
        client = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
        client.login()
    except GarminConnectAuthenticationError as e:
        raise GarminPullError(
            "Garmin login was rejected. Double-check GARMIN_EMAIL / GARMIN_PASSWORD "
            f"in .env. If you use MFA, provide the code when prompted. (details: {e})"
        ) from e
    except GarminConnectTooManyRequestsError as e:
        raise GarminPullError(
            "Garmin is rate-limiting logins (too many requests). Wait a while before "
            f"retrying. (details: {e})"
        ) from e
    except GarminConnectConnectionError as e:
        raise GarminPullError(
            f"Could not reach Garmin Connect. Check your network. (details: {e})"
        ) from e

    # Cache the session so the next run resumes instead of logging in again.
    try:
        TOKEN_STORE.parent.mkdir(parents=True, exist_ok=True)
        client.garth.dump(str(TOKEN_STORE))
    except Exception:
        pass  # Caching is best-effort; never fail the pull over it.

    return client


# Cached copy of the athlete's Garmin Connect profile image, shown as the
# dashboard logo (served by GET /athlete/avatar). Lives under data/ (gitignored);
# the extension follows the image's real content type.
AVATAR_DIR = ROOT / "data"
AVATAR_STEM = "athlete_avatar"
AVATAR_MAX_AGE_DAYS = 7


def avatar_file() -> Path | None:
    """The cached avatar image, or None when no sync has fetched one yet."""
    matches = sorted(AVATAR_DIR.glob(f"{AVATAR_STEM}.*"))
    return matches[0] if matches else None


def zones_plausible(zones: dict | None, duration_sec) -> bool:
    """
    Whether a time-in-zones payload can be trusted against the activity's
    duration. Garmin's endpoint returns near-empty numbers for multisport
    children (a 139-minute brick bike leg came back with 30 minutes of zones
    and 0 s in Z3-Z5 despite a max HR above the Z4 floor). An honest total can
    legitimately sit below the duration -- time under the Z1 floor is unzoned
    (coasting; most of a pool swim) -- so only a total under 60% of the
    duration is treated as broken; the observed failures are all under 30%,
    honest legs above 85%.
    """
    if not zones:
        return False
    try:
        dur = float(duration_sec or 0)
    except (TypeError, ValueError):
        return True
    if dur <= 0:
        return True  # nothing to compare against -- keep what Garmin said
    return sum(zones.values()) >= 0.6 * dur


def _vo2max_of(block) -> float | None:
    """One max-metrics sub-block's VO2max as a float, or None when it has none."""
    if not isinstance(block, dict):
        return None
    for key in _VO2MAX_KEYS:
        raw = block.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        # Garmin sends 0.0 for "no estimate yet". A zero is not a low VO2max --
        # charted as a real point it would flatten the whole y-axis.
        if 0 < value < 120:
            return value
    return None


def parse_max_metrics(payload) -> list[dict]:
    """
    Normalize Garmin's max-metrics payload to
    [{"date": "YYYY-MM-DD", "discipline": "run"|"bike", "vo2max": float}, ...],
    oldest first.

    Deliberately defensive: this is the one Garmin response the repo reads
    without a library method in front of it, its shape was never contracted, and
    it is expected to be partly empty (cycling VO2max needs a power meter). So
    the top level may be a list or a single dict, entries may be None, either
    sub-block may be missing, the calendar date may sit on the block or on the
    entry, and values may arrive as strings. Anything unreadable is skipped
    rather than raised on. Later entries win for the same (date, discipline), so
    a repeated day cannot duplicate.
    """
    entries = payload if isinstance(payload, list) else [payload]
    seen: dict[tuple[str, str], dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for block_key, discipline in _VO2MAX_BLOCKS:
            block = entry.get(block_key)
            value = _vo2max_of(block)
            if value is None:
                continue
            raw_date = block.get("calendarDate") or entry.get("calendarDate")
            try:
                day = date.fromisoformat(str(raw_date)).isoformat()
            except (TypeError, ValueError):
                continue
            seen[(day, discipline)] = {
                "date": day,
                "discipline": discipline,
                "vo2max": round(value, 1),
            }
    return sorted(seen.values(), key=lambda r: (r["date"], r["discipline"]))


def fetch_vo2max(client: Garmin, days: int = 28) -> list[dict]:
    """
    Best-effort daily running + cycling VO2max over the last `days` days, as
    [{"date", "discipline", "vo2max"}] oldest first.

    Same contract as fetch_hr_zones and refresh_avatar: never raises. A watch
    that has never produced an estimate, an athlete with no power meter, and a
    metrics endpoint that quietly changed shape all come back as fewer rows (or
    none) -- a sync that pulled activities fine must not fail over a panel.

    The raw payload is DEBUG-dumped per chunk. This endpoint is reached through
    connectapi rather than a typed library method, so that dump is the only
    record of what Garmin actually returned.
    """
    end = date.today()
    start = end - timedelta(days=max(1, days) - 1)
    rows: list[dict] = []

    span_start = start
    while span_start <= end:
        span_end = min(end, span_start + timedelta(days=VO2MAX_CHUNK_DAYS - 1))
        url = f"{VO2MAX_URL}/{span_start.isoformat()}/{span_end.isoformat()}"
        log.debug(
            "<<< Garmin request: max metrics %s..%s",
            span_start.isoformat(),
            span_end.isoformat(),
        )
        try:
            raw = client.connectapi(url)
        except Exception:
            log.warning(
                "Could not fetch VO2max for %s..%s",
                span_start.isoformat(),
                span_end.isoformat(),
                exc_info=True,
            )
            span_start = span_end + timedelta(days=1)
            continue
        if log.isEnabledFor(logging.DEBUG):
            try:
                dump = json.dumps(raw, indent=2, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                dump = str(raw)
            log.debug(
                ">>> Garmin response: max metrics %s..%s\n%s",
                span_start.isoformat(),
                span_end.isoformat(),
                dump,
            )
        rows.extend(parse_max_metrics(raw))
        span_start = span_end + timedelta(days=1)

    log.debug(
        "VO2max parsed: %d points (%d run, %d bike)",
        len(rows),
        sum(1 for r in rows if r["discipline"] == "run"),
        sum(1 for r in rows if r["discipline"] == "bike"),
    )
    return rows


def pull_vo2max(days: int = 28) -> list[dict]:
    """
    Daily running/cycling VO2max for the sync pipeline: connect, then fetch.

    The only library login left in the sync (workouts moved to the Personal
    API). With cached tokens in ~/.tricoach/garmin_tokens it is a token-file
    load, not a login; garth refreshes the short-lived token from the long-lived
    one on its own. The guard keeps a missing session from turning this
    best-effort call into a credential login that parks inside _prompt_mfa for
    MFA_WAIT_SECONDS. Without a session VO2max is simply skipped; running
    `python -m app.garmin.client` once (or a sync after deleting the token
    folder, with the MFA modal) creates it again.
    """
    if not TOKEN_STORE.exists():
        log.debug("No cached Garmin session -- skipping the VO2max pull")
        return []
    return fetch_vo2max(_connect(), days=days)


def _bounded(raw, bounds: tuple[float, float]) -> float | None:
    """A reading as a float inside `bounds`, or None (missing, zero, garbage)."""
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    lo, hi = bounds
    return value if lo <= value <= hi else None


def _day_of(raw) -> str | None:
    try:
        return date.fromisoformat(str(raw)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def parse_hrv(payload) -> list[dict]:
    """
    Normalize the nightly HRV payload to
    [{"date": "YYYY-MM-DD", "hrv": float}, ...], oldest first.

    The range endpoint returns {"hrvSummaries": [...]}; the single-day one
    returns {"hrvSummary": {...}}. Both are read. The value is lastNightAvg,
    the overnight average in ms -- not weeklyAvg (a smoothed 7-day figure) or
    lastNight5MinHigh (a peak).
    """
    payloads = payload if isinstance(payload, list) else [payload]
    seen: dict[str, dict] = {}
    for p in payloads:
        if not isinstance(p, dict):
            continue
        summaries = p.get("hrvSummaries")
        if not isinstance(summaries, list):
            summaries = [p.get("hrvSummary")] if "hrvSummary" in p else [p]
        for s in summaries:
            if not isinstance(s, dict):
                continue
            day = _day_of(s.get("calendarDate"))
            value = _bounded(s.get("lastNightAvg"), _HRV_BOUNDS)
            if day and value is not None:
                seen[day] = {"date": day, "hrv": round(value, 1)}
    return sorted(seen.values(), key=lambda r: r["date"])


if __name__ == "__main__":
    # Create (or check) the library session VO2max needs, then print 28 days of
    # it. A first login may ask for the emailed MFA code on this terminal.
    from dotenv import load_dotenv

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_dotenv()
    os.environ.setdefault("GARMIN_MFA_INTERACTIVE", "1")
    try:
        rows = fetch_vo2max(_connect(), days=28)
    except GarminPullError as err:
        print(f"\n[garmin] {err}")
        raise SystemExit(1)
    if not rows:
        print("No VO2max data in the last 28 days.")
    for r in rows:
        print(f"  {r['date']}  {r['discipline']:5}  {r['vo2max']:5.1f}")
