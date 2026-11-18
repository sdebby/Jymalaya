"""
Weather for an event's race location/date via Open-Meteo (free, no API key).

Dispatch (resolves PRD open question #3 as "use historical averages, don't skip"):
- race within the forecast horizon (0..16 days ahead) -> daily forecast
- further out (or in the past) -> historical climate normals, averaged from the
  archive API over the same calendar date across recent years

Network/parse failures never raise -- they return a Weather with source
"unavailable" so the pipeline (and the Claude payload in M6) degrades gracefully.

The route reads through `cached_weather_for_event`: at most one Open-Meteo
lookup per event per calendar day. The key carries the event's date and
location, so editing either fetches again at once; a result with source
"unavailable" is never cached, so a provider error is retried on the next load.
"""

from __future__ import annotations

from datetime import date, datetime
from threading import Lock

import httpx

from app.models import Weather

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

FORECAST_HORIZON_DAYS = 16
CLIMATE_YEARS = 5
_TIMEOUT = httpx.Timeout(12.0)


def geocode(name: str) -> tuple[float, float, str] | None:
    """Resolve a place name to (lat, lon, resolved_name). None on failure."""
    try:
        r = httpx.get(GEOCODE_URL, params={"name": name, "count": 1}, timeout=_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results") or []
    except (httpx.HTTPError, ValueError):
        return None
    if not results:
        return None
    top = results[0]
    label = ", ".join(p for p in (top.get("name"), top.get("country")) if p)
    return float(top["latitude"]), float(top["longitude"]), label or name


def _avg(values: list) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 1) if nums else None


def _forecast(lat: float, lon: float, target: date) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "auto",
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "apparent_temperature_max",
                "precipitation_probability_max",
                "wind_speed_10m_max",
                "wind_direction_10m_dominant",
                "uv_index_max",
            ]
        ),
        "hourly": "relative_humidity_2m",
    }
    r = httpx.get(FORECAST_URL, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    daily = data.get("daily", {})

    def d0(key):
        arr = daily.get(key) or []
        return arr[0] if arr else None

    tmax, tmin = d0("temperature_2m_max"), d0("temperature_2m_min")
    temp = _avg([tmax, tmin]) if (tmax is not None and tmin is not None) else tmax
    humidity = _avg(data.get("hourly", {}).get("relative_humidity_2m") or [])

    return {
        "temp_c": temp,
        "apparent_temp_c": d0("apparent_temperature_max"),
        "wind_kmh": d0("wind_speed_10m_max"),
        "wind_dir_deg": d0("wind_direction_10m_dominant"),
        "precipitation_probability_pct": d0("precipitation_probability_max"),
        "humidity_pct": humidity,
        "uv_index": d0("uv_index_max"),
    }


def _climate_normals(lat: float, lon: float, target: date) -> dict:
    """Average the same month/day across the last CLIMATE_YEARS archived years."""
    temps, tmins, winds, precs = [], [], [], []
    this_year = date.today().year
    for yr in range(this_year - 1, this_year - 1 - CLIMATE_YEARS, -1):
        try:
            day = target.replace(year=yr)
        except ValueError:  # Feb 29 in a non-leap archive year
            day = target.replace(year=yr, day=28)
        params = {
            "latitude": lat,
            "longitude": lon,
            "timezone": "auto",
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "daily": "temperature_2m_max,temperature_2m_min,wind_speed_10m_max,precipitation_sum",
        }
        try:
            r = httpx.get(ARCHIVE_URL, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            daily = r.json().get("daily", {})
        except (httpx.HTTPError, ValueError):
            continue
        for src, dst in (
            ("temperature_2m_max", temps),
            ("temperature_2m_min", tmins),
            ("wind_speed_10m_max", winds),
            ("precipitation_sum", precs),
        ):
            arr = daily.get(src) or []
            if arr and arr[0] is not None:
                dst.append(arr[0])

    tmax, tmin = _avg(temps), _avg(tmins)
    temp = _avg([tmax, tmin]) if (tmax is not None and tmin is not None) else tmax
    # crude precip-probability proxy: share of sampled years with measurable rain
    prec_prob = (
        round(sum(1 for p in precs if p and p > 0.5) / len(precs) * 100, 0)
        if precs
        else None
    )
    return {
        "temp_c": temp,
        "apparent_temp_c": None,
        "wind_kmh": _avg(winds),
        "wind_dir_deg": None,
        "precipitation_probability_pct": prec_prob,
        "humidity_pct": None,
        "uv_index": None,
    }


def weather_for_event(event: dict) -> Weather:
    """Build a Weather for an event dict (must carry lat/lon or a geocodable name)."""
    event_id = event.get("event_id", "")
    race_date = event.get("race_date") or ""
    lat, lon, name = event.get("location_lat"), event.get("location_lon"), event.get("location_name")

    base = Weather(event_id=event_id, race_date=race_date, source="unavailable",
                   location_lat=lat, location_lon=lon, location_name=name)

    # Resolve coordinates from a place name if needed.
    if (lat is None or lon is None) and name:
        geo = geocode(name)
        if geo:
            lat, lon, name = geo
            base.location_lat, base.location_lon, base.location_name = lat, lon, name
    if lat is None or lon is None:
        base.note = "No location (lat/lon or geocodable name) on the event."
        return base

    try:
        target = datetime.strptime(race_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        base.note = "Event has no valid race_date."
        return base

    days_until = (target - date.today()).days
    base.days_until_race = days_until

    try:
        if 0 <= days_until <= FORECAST_HORIZON_DAYS:
            fields, source = _forecast(lat, lon, target), "forecast"
        else:
            fields, source = _climate_normals(lat, lon, target), "historical_climate"
    except (httpx.HTTPError, ValueError, KeyError) as e:
        base.note = f"Weather provider error: {type(e).__name__}"
        return base

    return base.model_copy(update={**fields, "source": source})


# (day, event_id, race_date, lat, lon, name) -> Weather. In memory: a restart
# simply fetches again. Only today's entries are ever kept.
_CACHE: dict[tuple, Weather] = {}
_CACHE_LOCK = Lock()


def cached_weather_for_event(event: dict) -> Weather:
    """`weather_for_event`, looked up at most once per event per day."""
    today = date.today()
    key = (today.isoformat(), event.get("event_id", ""), event.get("race_date") or "",
           event.get("location_lat"), event.get("location_lon"), event.get("location_name"))
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit
    weather = weather_for_event(event)
    if weather.source != "unavailable":
        with _CACHE_LOCK:
            for old in [k for k in _CACHE if k[0] != key[0]]:
                del _CACHE[old]
            _CACHE[key] = weather
    return weather
