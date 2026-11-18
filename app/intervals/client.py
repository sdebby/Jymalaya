"""
intervals.icu API client -- the bridge behind the dashboard's Send to device.

A planned session is written to the athlete's intervals.icu calendar as a
structured WORKOUT event; intervals.icu pushes it on to Garmin Connect, and from
there it reaches the watch. The API key is the athlete's own (INTERVALS_API_KEY
in .env), sent only to intervals.icu and never logged.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("jymalaya.intervals")

BASE_URL = "https://intervals.icu/api/v1"
ATHLETE_ID = "0"  # "0" is the athlete who owns the API key
# Cloudflare in front of intervals.icu rejects the default client user agents.
USER_AGENT = "Jymalaya-AI-Coach/1.0"
_TIMEOUT = httpx.Timeout(30.0)
# How much of an error body is passed on to the dashboard.
_ERROR_BODY_CHARS = 300


class IntervalsError(RuntimeError):
    """A call to intervals.icu failed; the message is safe to show the athlete."""


class IntervalsNotConfigured(IntervalsError):
    """INTERVALS_API_KEY is not set."""


def _api_key() -> str:
    key = (os.getenv("INTERVALS_API_KEY") or "").strip()
    if not key:
        raise IntervalsNotConfigured("INTERVALS_API_KEY is not set in .env.")
    return key


def upsert_workout(event: dict) -> dict:
    """
    Put the event on the calendar and return it as intervals.icu stored it.

    `upsert=true` matches on the event's external_id, so sending the same plan
    session again updates the entry sent earlier instead of adding a second one.
    """
    key = _api_key()
    log.debug(
        "intervals.icu upsert %s (%s):\n%s",
        event.get("external_id"),
        event.get("start_date_local"),
        event.get("description"),
    )
    try:
        r = httpx.post(
            f"{BASE_URL}/athlete/{ATHLETE_ID}/events/bulk",
            params={"upsert": "true"},
            json=[event],
            auth=("API_KEY", key),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise IntervalsError(f"intervals.icu could not be reached ({type(e).__name__}).") from e
    if r.status_code in (401, 403):
        raise IntervalsError(
            f"intervals.icu rejected the API key (HTTP {r.status_code}). "
            "Check INTERVALS_API_KEY in .env."
        )
    if r.is_error:
        detail = r.text.strip()[:_ERROR_BODY_CHARS]
        raise IntervalsError(f"intervals.icu refused the workout (HTTP {r.status_code}). {detail}".strip())
    try:
        body = r.json()
    except ValueError as e:
        raise IntervalsError("intervals.icu answered with something that is not JSON.") from e
    stored = body[0] if isinstance(body, list) and body else body
    if not isinstance(stored, dict):
        raise IntervalsError("intervals.icu answered without the event.")
    log.info(
        "intervals.icu event %s: %s on %s (moving time %s s)",
        stored.get("id"),
        stored.get("name"),
        stored.get("start_date_local"),
        stored.get("moving_time"),
    )
    return stored
