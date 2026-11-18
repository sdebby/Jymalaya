"""Shared test setup."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app import instructions as app_instructions
from app import plan_requests, sync_service
from app.garmin import personal_api
from app.season import instructions as season_instructions
from app.season import store as season_store


@pytest.fixture(autouse=True)
def _isolated_plan_requests(tmp_path, monkeypatch):
    """
    Every plan edit saves the edited day as an athlete request, and every plan
    payload reads them back. Point the store at a temp file for every test, so
    no test writes into the athlete's live data/ or depends on what is there.
    """
    monkeypatch.setattr(
        plan_requests, "REQUESTS_FILE", tmp_path / "plan_requests.json"
    )


@pytest.fixture(autouse=True)
def _isolated_season_plan(tmp_path, monkeypatch):
    """
    The season plan is one file in data/. Every test gets an empty temp one, so
    no test writes the athlete's season or reads it -- and with no plan, nothing
    that existed before the season plan changes behaviour.
    """
    monkeypatch.setattr(
        season_store, "SEASON_PLAN_FILE", tmp_path / "season_plan.json"
    )


@pytest.fixture(autouse=True)
def _default_activity_class(monkeypatch):
    """
    A new season plan takes its blank defaults from the profile's activity
    class, and config/athlete.local.yaml holds the athlete's real one on a
    developer machine. Pin it at the profile default (4), which gives exactly
    the constants' plan. tests/test_season_activity_class.py re-patches it.
    """
    from app.season import service as season_service

    monkeypatch.setattr(season_service, "_activity_class", lambda: 4)


@pytest.fixture(autouse=True)
def _no_recovery_pull(monkeypatch):
    """
    The sync's HRV / resting-HR pull resumes a cached Garmin session when one
    exists, so any test that runs the pipeline without patching it would make
    a real network call on a developer machine. tests/test_recovery.py
    re-patches it where the pull itself is under test.
    """
    monkeypatch.setattr(sync_service, "pull_recovery", lambda *a, **kw: [])
    monkeypatch.setattr(sync_service, "pull_sleep", lambda *a, **kw: [])


@pytest.fixture(autouse=True)
def _no_personal_api(monkeypatch):
    """
    The sync's workouts, FIT files and recovery come from Garmin's Personal API,
    and a developer machine has a real key in .env. No test may reach it: every
    request path answers as if the network were down. tests/test_personal_api.py
    restores the real functions over a fake httpx.
    """
    def offline(*a, **kw):
        raise personal_api.PersonalApiError("network", "tests are offline", personal_api.ACTION_RETRY)

    monkeypatch.setattr(personal_api, "_request", offline)
    monkeypatch.setattr(personal_api, "exchange_key", offline)
    monkeypatch.setattr(personal_api, "FIT_TMP", Path(tempfile.gettempdir()) / "jymalaya-test-fit")


@pytest.fixture(autouse=True)
def _isolated_coach_instructions(tmp_path, monkeypatch):
    """
    The season plan reads the athlete's coach instructions with a Claude call,
    and data/coach_instructions.txt holds real text on a developer machine. Point
    the file at an empty temp one, and stub the call (an empty read) with a
    fresh cache, so no test reads the athlete's text or reaches the network.
    tests/test_season_instructions.py re-patches the call where the read itself
    is under test.
    """
    monkeypatch.setattr(app_instructions, "INSTRUCTIONS_FILE", tmp_path / "coach_instructions.txt")
    monkeypatch.setattr(season_instructions, "_ask_claude", lambda text, context: {})
    monkeypatch.setattr(season_instructions, "_CACHE", {})


@pytest.fixture(autouse=True)
def _fresh_weather_cache(monkeypatch):
    """The weather route caches per event per day in memory; start every test empty."""
    from app.weather import openmeteo

    monkeypatch.setattr(openmeteo, "_CACHE", {})
