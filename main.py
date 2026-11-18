"""
Jymalaya AI Coach -- FastAPI entry point.

Run locally:  uvicorn main:app --reload --port 8080
Requires InfluxDB up:  docker compose up -d influxdb

In the container the same process also serves the built dashboard: the SPA is
copied to frontend_dist/ and mounted at /, while the whole REST API lives
under /api (see API_PREFIX below).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

# App-namespace logging to the uvicorn terminal. DEBUG makes app.ai.claude dump
# full Claude API inputs/outputs; LOG_LEVEL=INFO silences the dumps (what the
# container sets -- those dumps are large and carry the whole plan payload).
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"))
_app_log = logging.getLogger("jymalaya")
_app_log.addHandler(_handler)
_app_log.setLevel(os.getenv("LOG_LEVEL", "DEBUG").upper())

from app.routers import (  # noqa: E402  (after load_dotenv)
    analysis,
    athlete,
    coach,
    data,
    device,
    events,
    health,
    recommend,
    schedule,
    season,
    sync,
    weather,
)
from app.garmin.personal_api import purge_fit_tmp  # noqa: E402

# Every route is served under /api so the SPA can be mounted at /. The frontend
# addresses the backend as "/api" in both modes -- the Vite dev proxy forwards
# the prefix untouched, and in the container this process serves both. Do not
# move the routers back to the root: it silently 404s the whole dashboard.
API_PREFIX = "/api"

# Built dashboard, present only in the container image (Dockerfile copies the
# Vite build here). Absent in dev, where Vite serves the SPA on :5173.
ROOT = Path(__file__).resolve().parent
FRONTEND_DIST = ROOT / "frontend_dist"

app = FastAPI(title="Jymalaya AI Coach", version="0.1.0")

# A FIT file is deleted right after it is decoded; one left behind means a
# crash in between. Only the samples it was decoded into are kept (InfluxDB).
purge_fit_tmp()

# CORS matters only when the SPA is on a different origin -- i.e. the Vite dev
# server. In the container the dashboard is same-origin and this is unused.
_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix=API_PREFIX)
api.include_router(sync.router)
api.include_router(health.router)
api.include_router(events.router)
api.include_router(weather.router)
api.include_router(recommend.router)
api.include_router(coach.router)
api.include_router(analysis.router)
api.include_router(athlete.router)
api.include_router(schedule.router)
api.include_router(data.router)
api.include_router(device.router)
api.include_router(season.router)


@api.get("")
def root():
    return {"service": "Jymalaya AI Coach", "docs": "/docs"}


app.include_router(api)

# Mounted last so /api, /docs and /openapi.json keep priority -- Starlette
# matches routes in registration order, and this one matches everything.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
