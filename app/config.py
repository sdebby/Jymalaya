"""
Centralized config + settings loading.

- Secrets and connection settings come from the environment (.env, loaded by
  python-dotenv in main.py / module __main__ blocks).
- The non-secret athlete profile comes from versioned YAML under config/,
  overridable by a gitignored *.local.yaml sibling. The weekly schedule used to
  live here too; it is now athlete-editable JSON (app/schedule_store.py).
"""

from __future__ import annotations

import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

# Repo root = two levels up from this file (app/config.py -> app -> root).
ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


class Settings:
    """Environment-derived connection settings (InfluxDB, app)."""

    def __init__(self) -> None:
        self.influx_url = os.getenv("INFLUXDB_URL", "http://localhost:8086")
        self.influx_token = _influx_token_from_file() or os.getenv(
            "INFLUXDB_TOKEN", "tricoach-dev-token"
        )
        self.influx_org = os.getenv("INFLUXDB_ORG", "tricoach")
        self.influx_bucket = os.getenv("INFLUXDB_BUCKET", "tricoach")
        self.app_port = int(os.getenv("APP_PORT", "8080"))


def _influx_token_from_file() -> str | None:
    """
    The token in the file INFLUXDB_TOKEN_FILE names, if it is set and readable.

    The single-container Jymalaya-C1 provisions its own InfluxDB on first start
    and keeps the generated token in its data volume; the file wins over
    INFLUXDB_TOKEN so a copied dev .env cannot point the app at the wrong one,
    and so `docker exec ... python scripts/...` finds it too.
    """
    path = os.getenv("INFLUXDB_TOKEN_FILE")
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def get_settings() -> Settings:
    # Not cached: tests/runtime may reload env. Cheap to construct.
    return Settings()


def _load_yaml(name: str) -> dict:
    """Load config/<name>.yaml, letting config/<name>.local.yaml override it."""
    local = CONFIG_DIR / f"{name}.local.yaml"
    base = CONFIG_DIR / f"{name}.yaml"
    path = local if local.exists() else base
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# Oldest birthday accepted as a real one; anything older is a typo or a
# placeholder date, not an athlete.
MAX_AGE_YEARS = 120

# Habitual activity level (0-10) for a profile saved before the field existed.
DEFAULT_ACTIVITY_CLASS = 4

# How far Garmin's VO2max estimate must fall across the 4-week trend window
# before the plan prompt calls it a decline (ml/kg/min). The athlete edits this
# in Settings; this is the default for a profile saved before the field existed.
# Garmin revises the estimate in small steps, so a smaller change is noise, and
# a VO2max session must not be scheduled on noise.
DEFAULT_VO2MAX_DECLINE_THRESHOLD = 0.5


def age_on(birthday: date, today: date | None = None) -> int:
    """Completed years between `birthday` and `today` (defaults to today)."""
    today = today or date.today()
    before_birthday = (today.month, today.day) < (birthday.month, birthday.day)
    return today.year - birthday.year - (1 if before_birthday else 0)


def _parse_birthday(value) -> date | None:
    """A YAML date, a datetime, or an ISO string -> date; anything else -> None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


@lru_cache(maxsize=1)
def _athlete_yaml() -> dict:
    return _load_yaml("athlete")


def get_athlete_profile() -> dict:
    """
    FTP, threshold pace, CSS, LTHR, weight, birthday (config/athlete.yaml), plus
    the `age` derived from the birthday.

    Age is computed on every read rather than stored, so it can never go stale
    between profile edits -- a stored number is wrong from the athlete's next
    birthday until they notice and re-type it.

    A profile saved before `activity_class` or `vo2max_decline_threshold`
    existed has no such key (the local YAML replaces athlete.yaml rather than
    merging with it), so the defaults are filled in here -- otherwise every
    Claude payload goes without them while Settings shows the model's default.
    """
    profile = dict(_athlete_yaml())
    profile.setdefault("activity_class", DEFAULT_ACTIVITY_CLASS)
    profile.setdefault(
        "vo2max_decline_threshold", DEFAULT_VO2MAX_DECLINE_THRESHOLD
    )
    birthday = _parse_birthday(profile.get("birthday"))
    if birthday is None:
        # Profiles written before the birthday field stored a bare `age`. Keep
        # them working by putting the birthday on Jan 1 of the implied birth
        # year: it reproduces the stored age exactly for the rest of this
        # calendar year, and is replaced the first time the athlete saves a real
        # birthday from Settings.
        stored_age = profile.pop("age", None)
        if isinstance(stored_age, (int, float)):
            birthday = date(date.today().year - int(stored_age), 1, 1)
    if birthday is not None:
        profile["birthday"] = birthday.isoformat()
        profile["age"] = age_on(birthday)
    return profile


def save_athlete_profile(profile: dict) -> None:
    """
    Persist the athlete profile to config/athlete.local.yaml (the gitignored
    override _load_yaml prefers), leaving the versioned athlete.yaml untouched.
    `age` is dropped: it is derived from the birthday, never stored.
    """
    stored = {k: v for k, v in profile.items() if k != "age"}
    path = CONFIG_DIR / "athlete.local.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(stored, f, sort_keys=False)
    _athlete_yaml.cache_clear()
