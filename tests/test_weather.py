"""Unit tests for weather dispatch (provider calls stubbed -- no network)."""

from datetime import date, timedelta

import app.weather.openmeteo as om


def _goal(race_date: str, **kw):
    base = {"event_id": "g1", "race_date": race_date, "location_lat": 46.6, "location_lon": 14.3}
    base.update(kw)
    return base


def test_no_location_returns_unavailable():
    w = om.weather_for_event({"event_id": "g", "race_date": "2026-09-06"})
    assert w.source == "unavailable"
    assert "location" in (w.note or "").lower()


def test_near_race_uses_forecast(monkeypatch):
    monkeypatch.setattr(om, "_forecast", lambda lat, lon, target: {"temp_c": 27.0})
    monkeypatch.setattr(om, "_climate_normals", lambda *a: {"temp_c": -999})
    soon = (date.today() + timedelta(days=5)).isoformat()
    w = om.weather_for_event(_goal(soon))
    assert w.source == "forecast"
    assert w.temp_c == 27.0
    assert w.days_until_race == 5


def test_far_race_uses_climate(monkeypatch):
    monkeypatch.setattr(om, "_forecast", lambda *a: {"temp_c": -999})
    monkeypatch.setattr(om, "_climate_normals", lambda lat, lon, target: {"temp_c": 24.5})
    far = (date.today() + timedelta(days=60)).isoformat()
    w = om.weather_for_event(_goal(far))
    assert w.source == "historical_climate"
    assert w.temp_c == 24.5


def test_geocode_fills_coordinates(monkeypatch):
    monkeypatch.setattr(om, "geocode", lambda name: (46.62, 14.31, "Klagenfurt, Austria"))
    monkeypatch.setattr(om, "_forecast", lambda *a: {"temp_c": 20.0})
    soon = (date.today() + timedelta(days=3)).isoformat()
    g = {"event_id": "g", "race_date": soon, "location_name": "Klagenfurt"}
    w = om.weather_for_event(g)
    assert w.location_lat == 46.62
    assert w.source == "forecast"


def test_invalid_race_date_unavailable():
    w = om.weather_for_event(_goal("not-a-date"))
    assert w.source == "unavailable"


def _counting_forecast(monkeypatch, temp=27.0):
    calls = []

    def fake(lat, lon, target):
        calls.append(target)
        return {"temp_c": temp}

    monkeypatch.setattr(om, "_forecast", fake)
    return calls


def test_cached_weather_fetches_once_per_day(monkeypatch):
    calls = _counting_forecast(monkeypatch)
    soon = (date.today() + timedelta(days=5)).isoformat()
    first = om.cached_weather_for_event(_goal(soon))
    second = om.cached_weather_for_event(_goal(soon))
    assert len(calls) == 1
    assert first.temp_c == second.temp_c == 27.0


def test_cached_weather_refetches_on_a_new_day(monkeypatch):
    calls = _counting_forecast(monkeypatch)
    soon = (date.today() + timedelta(days=5)).isoformat()
    om.cached_weather_for_event(_goal(soon))
    tomorrow = date.today() + timedelta(days=1)

    class _Date(date):
        @classmethod
        def today(cls):
            return tomorrow

    monkeypatch.setattr(om, "date", _Date)
    om.cached_weather_for_event(_goal(soon))
    assert len(calls) == 2
    assert len(om._CACHE) == 1  # yesterday's entry is pruned


def test_cached_weather_refetches_after_an_event_edit(monkeypatch):
    calls = _counting_forecast(monkeypatch)
    soon = (date.today() + timedelta(days=5)).isoformat()
    om.cached_weather_for_event(_goal(soon))
    om.cached_weather_for_event(_goal(soon, location_lat=32.1, location_lon=34.8))
    assert len(calls) == 2


def test_unavailable_is_not_cached(monkeypatch):
    def failing(*a):
        raise om.httpx.ConnectError("offline")

    monkeypatch.setattr(om, "_forecast", failing)
    soon = (date.today() + timedelta(days=5)).isoformat()
    assert om.cached_weather_for_event(_goal(soon)).source == "unavailable"
    calls = _counting_forecast(monkeypatch)
    assert om.cached_weather_for_event(_goal(soon)).source == "forecast"
    assert len(calls) == 1
