"""
Athlete birthday -> derived age: the arithmetic, the legacy `age:` fallback for
profiles written before the birthday field, the GET/PUT endpoints, and what the
Claude payload carries. Fakes only -- CONFIG_DIR is redirected at a tmp dir, so
the real profile on disk is never touched.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import yaml
from fastapi.testclient import TestClient

import app.schedule_store as store
from app import config
from main import app

# base_url carries the /api prefix the routers are mounted under.
client = TestClient(app, base_url="http://testserver/api")

THRESHOLDS = {
    "ftp_w": 250.0,
    "run_threshold_pace_sec_km": 270.0,
    "css_sec_100m": 95.0,
    "lthr_bpm": 162.0,
    "weight_kg": 72.0,
}


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    config._athlete_yaml.cache_clear()
    yield tmp_path
    config._athlete_yaml.cache_clear()  # don't leak the tmp profile into later tests


def _write_profile(config_dir, body: str) -> None:
    (config_dir / "athlete.yaml").write_text(
        yaml.safe_dump(THRESHOLDS) + body, encoding="utf-8"
    )
    config._athlete_yaml.cache_clear()


# --- the arithmetic ---------------------------------------------------------
def test_age_on_counts_completed_years():
    bd = date(1988, 6, 15)
    assert config.age_on(bd, date(2026, 6, 14)) == 37  # the day before
    assert config.age_on(bd, date(2026, 6, 15)) == 38  # on the day
    assert config.age_on(bd, date(2026, 12, 31)) == 38
    assert config.age_on(bd, date(2027, 1, 1)) == 38


def test_age_on_leap_day_birthday_rolls_on_march_1():
    bd = date(2000, 2, 29)
    assert config.age_on(bd, date(2026, 2, 28)) == 25
    assert config.age_on(bd, date(2026, 3, 1)) == 26


# --- profile loading --------------------------------------------------------
def test_profile_derives_age_from_birthday(config_dir):
    _write_profile(config_dir, 'birthday: "1988-06-15"\n')
    p = config.get_athlete_profile()
    assert p["birthday"] == "1988-06-15"
    assert p["age"] == config.age_on(date(1988, 6, 15))


def test_unquoted_yaml_date_is_accepted(config_dir):
    # YAML parses a bare 1988-06-15 into datetime.date, not a string.
    _write_profile(config_dir, "birthday: 1988-06-15\n")
    assert config.get_athlete_profile()["birthday"] == "1988-06-15"


def test_legacy_age_only_profile_still_loads(config_dir):
    _write_profile(config_dir, "age: 49\n")
    p = config.get_athlete_profile()
    # Jan 1 of the implied birth year -- reproduces the stored age exactly for
    # the rest of this calendar year.
    assert p["birthday"] == f"{date.today().year - 49}-01-01"
    assert p["age"] == 49


# --- API --------------------------------------------------------------------
def test_get_and_put_roundtrip(config_dir):
    _write_profile(config_dir, 'birthday: "1988-06-15"\n')
    got = client.get("/athlete").json()
    assert got["birthday"] == "1988-06-15"
    assert got["age"] == config.age_on(date(1988, 6, 15))

    r = client.put("/athlete", json={**THRESHOLDS, "birthday": "1990-02-01"})
    assert r.status_code == 200
    assert r.json()["birthday"] == "1990-02-01"
    assert r.json()["age"] == config.age_on(date(1990, 2, 1))

    saved = yaml.safe_load((config_dir / "athlete.local.yaml").read_text(encoding="utf-8"))
    assert saved["birthday"] == "1990-02-01"
    assert "age" not in saved  # derived on read, never stored
    assert client.get("/athlete").json()["birthday"] == "1990-02-01"


def test_put_rejects_a_birthday_that_is_not_a_real_past_date(config_dir):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.put("/athlete", json={**THRESHOLDS, "birthday": tomorrow}).status_code == 422
    assert client.put("/athlete", json={**THRESHOLDS, "birthday": "1800-01-01"}).status_code == 422
    assert client.put("/athlete", json={**THRESHOLDS, "birthday": "not a date"}).status_code == 422


# --- InfluxDB mirror --------------------------------------------------------
@pytest.fixture
def mirror_writes(monkeypatch):
    """Profiles written to the athlete_profile measurement by PUT /athlete."""
    from app import settings_editor

    writes: list[dict] = []

    class FakeRepo:
        def write_athlete_profile(self, p):
            writes.append(p)

        def close(self):
            pass

    monkeypatch.setattr(settings_editor, "InfluxRepo", FakeRepo)
    return writes


def test_put_mirrors_the_profile_only_when_a_value_changed(config_dir, mirror_writes):
    _write_profile(config_dir, 'birthday: "1988-06-15"\n')
    body = {**THRESHOLDS, "birthday": "1988-06-15"}

    # Saving the profile as it already is (Save with no edits) writes nothing,
    # so every point in the measurement is a real change.
    assert client.put("/athlete", json=body).status_code == 200
    assert mirror_writes == []

    client.put("/athlete", json={**body, "ftp_w": 260})
    assert len(mirror_writes) == 1
    assert mirror_writes[0]["ftp_w"] == 260

    client.put("/athlete", json={**body, "ftp_w": 260})  # same again
    assert len(mirror_writes) == 1
    # The sync never writes it: the pipeline fakes in test_sync_phase and
    # friends have no write_athlete_profile, so a call there would fail them.


# --- activity class ---------------------------------------------------------
def test_activity_class_defaults_to_4_when_the_profile_predates_it(config_dir):
    _write_profile(config_dir, 'birthday: "1988-06-15"\n')
    assert client.get("/athlete").json()["activity_class"] == 4
    # Filled in on read too, not only by the API model -- the Claude payloads
    # read the profile directly and would otherwise go without it.
    assert config.get_athlete_profile()["activity_class"] == 4


def test_activity_class_roundtrips_including_zero(config_dir):
    _write_profile(config_dir, 'birthday: "1988-06-15"\n')
    body = {**THRESHOLDS, "birthday": "1988-06-15", "activity_class": 0}
    r = client.put("/athlete", json=body)
    assert r.status_code == 200
    assert r.json()["activity_class"] == 0  # 0 is a real answer, not "unset"

    saved = yaml.safe_load((config_dir / "athlete.local.yaml").read_text(encoding="utf-8"))
    assert saved["activity_class"] == 0
    assert client.get("/athlete").json()["activity_class"] == 0

    assert client.put("/athlete", json={**body, "activity_class": 10}).json()["activity_class"] == 10


def test_put_rejects_an_activity_class_outside_0_to_10(config_dir):
    body = {**THRESHOLDS, "birthday": "1988-06-15"}
    assert client.put("/athlete", json={**body, "activity_class": 11}).status_code == 422
    assert client.put("/athlete", json={**body, "activity_class": -1}).status_code == 422


# --- VO2max decline threshold -----------------------------------------------
def test_vo2max_decline_threshold_defaults_when_the_profile_predates_it(config_dir):
    _write_profile(config_dir, 'birthday: "1988-06-15"\n')
    default = config.DEFAULT_VO2MAX_DECLINE_THRESHOLD
    assert client.get("/athlete").json()["vo2max_decline_threshold"] == default
    # Filled in on read too, not only by the API model -- build_plan_payload
    # reads the profile directly and would otherwise classify the trend against
    # nothing at all.
    assert config.get_athlete_profile()["vo2max_decline_threshold"] == default


def test_vo2max_decline_threshold_roundtrips_a_tenth(config_dir):
    _write_profile(config_dir, 'birthday: "1988-06-15"\n')
    body = {**THRESHOLDS, "birthday": "1988-06-15", "vo2max_decline_threshold": 0.2}
    r = client.put("/athlete", json=body)
    assert r.status_code == 200
    assert r.json()["vo2max_decline_threshold"] == 0.2  # tenths, not whole units

    saved = yaml.safe_load((config_dir / "athlete.local.yaml").read_text(encoding="utf-8"))
    assert saved["vo2max_decline_threshold"] == 0.2
    assert client.get("/athlete").json()["vo2max_decline_threshold"] == 0.2


def test_put_rejects_a_vo2max_decline_threshold_outside_0_to_5(config_dir):
    body = {**THRESHOLDS, "birthday": "1988-06-15"}
    # 0 would fire on every wobble; 50 is a typo that disables the rule for good.
    assert client.put("/athlete", json={**body, "vo2max_decline_threshold": 0}).status_code == 422
    assert client.put("/athlete", json={**body, "vo2max_decline_threshold": 50}).status_code == 422


# --- Claude payload ---------------------------------------------------------
def test_plan_payload_carries_age_not_birthday(config_dir, monkeypatch):
    from app.ai import prompt_builder as pb

    class FakeRepo:
        def query(self, flux):
            return []

        def close(self):
            pass

    _write_profile(config_dir, 'birthday: "1988-06-15"\n')
    monkeypatch.setattr(pb, "InfluxRepo", FakeRepo)
    monkeypatch.setattr(pb.events_service, "active_event", lambda: None)
    # last_race reaches events_service.InfluxRepo directly, which patching
    # pb.InfluxRepo does not cover -- without this the test needs a live DB.
    monkeypatch.setattr(pb.events_service, "last_race", lambda **kw: None)
    # Likewise get_schedule, which reads (and on a fresh clone writes) the real
    # data/schedule.json.
    monkeypatch.setattr(pb, "get_schedule", lambda: store._normalize(store.DEFAULT_SCHEDULE))

    profile = pb.build_plan_payload()["athlete_profile"]
    assert profile["age"] == config.age_on(date(1988, 6, 15))
    assert "birthday" not in profile
    assert profile["activity_class"] == 4  # the file predates the field
    assert (
        profile["vo2max_decline_threshold"]
        == config.DEFAULT_VO2MAX_DECLINE_THRESHOLD
    )  # likewise
