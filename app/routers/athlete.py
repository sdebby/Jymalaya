"""Athlete profile endpoints (thresholds edited from the dashboard Settings)."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_athlete_profile
from app.garmin.client import avatar_file
from app.models import AthleteProfile
from app.settings_editor import save_profile

router = APIRouter(tags=["athlete"])


@router.get("/athlete", response_model=AthleteProfile)
def get_athlete():
    return get_athlete_profile()


@router.get("/athlete/avatar")
def get_avatar():
    """The athlete's Garmin Connect profile image, cached by the sync."""
    f = avatar_file()
    if f is None:
        raise HTTPException(404, "No cached Garmin profile image yet -- run a sync.")
    # no-cache: the browser revalidates (304 via Last-Modified/ETag) so a new
    # image picked up by a sync shows without a hard refresh.
    return FileResponse(
        f,
        media_type=mimetypes.guess_type(f.name)[0] or "image/jpeg",
        headers={"Cache-Control": "no-cache"},
    )


@router.put("/athlete", response_model=AthleteProfile)
def update_athlete(body: AthleteProfile):
    # Shared with the coach chat's settings tool (app/settings_editor.py).
    return save_profile(body)
