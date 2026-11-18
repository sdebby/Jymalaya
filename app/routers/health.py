"""Health + minimal metrics read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.influx.client import InfluxRepo, drop_split_brick_parents
from app.influx.queries import (
    daily_load_last_n_days,
    recovery_last_n_days,
    vo2max_last_n_days,
    workouts_last_n_days,
)
from app.metrics.calculator import pad_load_to
from app.sync_service import get_status, recompute_stored_metrics

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    repo = InfluxRepo()
    try:
        influx_ok = repo.ping()
    finally:
        repo.close()
    status = get_status()
    return {
        "status": "ok" if influx_ok else "degraded",
        "influxdb_connected": influx_ok,
        "last_sync": status.get("last_sync"),
        "sync_status": status.get("status"),
    }


@router.get("/metrics/workouts")
def metrics_workouts(days: int = 28):
    """Minimal read-back of stored workouts (full /metrics/* lands in M7)."""
    repo = InfluxRepo()
    try:
        return drop_split_brick_parents(repo.query(workouts_last_n_days(days)))
    finally:
        repo.close()


@router.get("/metrics/load")
def metrics_load(days: int = 28, discipline: str = "all"):
    """
    Daily CTL/ATL/TSB, always ending today. The stored series is only rebuilt by
    a sync or a recompute, so it is padded forward with zero-TSS rest days on
    read -- otherwise the dashboard's last point (labelled "now") drifts back
    into the past between syncs.
    """
    repo = InfluxRepo()
    try:
        return pad_load_to(repo.query(daily_load_last_n_days(days, discipline)))
    finally:
        repo.close()


@router.get("/metrics/vo2max")
def metrics_vo2max(days: int = 28):
    """
    Daily running/cycling VO2max as Garmin reports it, over the last `days`
    days: [{time, discipline, vo2max}] oldest first.

    Sparse by nature -- Garmin only revises the estimate after a qualifying
    activity, so most days carry no row and the dashboard carries each reading
    forward across the gaps. Cycling rows exist only for an athlete riding with
    a power meter, so an all-running result (or an empty one) is a normal
    answer, not an error.

    `time` is midnight UTC: it IS the calendar day, and must not be converted
    through a local timezone on read.
    """
    repo = InfluxRepo()
    try:
        return repo.query(vo2max_last_n_days(days))
    finally:
        repo.close()


@router.get("/metrics/recovery")
def metrics_recovery(days: int = 28):
    """
    Daily overnight HRV (ms) and resting heart rate (bpm) over the last `days`
    days: [{time, hrv?, resting_hr?}] oldest first, for the dashboard's
    recovery-trend cards. A day Garmin had no reading for has no row (or no
    column for that field); the dashboard draws it as a gap, never a zero.

    `time` is midnight UTC, the calendar day itself, as for /metrics/vo2max.
    Dashboard only: none of this reaches a Claude payload.
    """
    repo = InfluxRepo()
    try:
        return repo.query(recovery_last_n_days(days))
    finally:
        repo.close()


@router.post("/metrics/recompute")
def metrics_recompute(days: int = 7):
    """
    Recompute TSS/IF (and swim pace) for the stored workouts of the last
    `days` days from the current athlete profile, then rebuild the daily
    CTL/ATL/TSB series from the full history. Triggered by the Settings
    "Re-calculate" button after a threshold edit; no Garmin pull involved.
    """
    repo = InfluxRepo()
    try:
        return recompute_stored_metrics(repo, days=days)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Recompute failed: {e}") from e
    finally:
        repo.close()
