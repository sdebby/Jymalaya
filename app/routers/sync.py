"""
Sync endpoints: trigger a Garmin sync, report its status, submit an MFA code,
save a new Garmin Personal API key.
"""

from __future__ import annotations

import os
import re

from dotenv import set_key
from fastapi import APIRouter, HTTPException

from app import sync_service
from app.config import ROOT
from app.garmin import client as garmin_client
from app.garmin import personal_api
from app.garmin.client import GarminPullError
from app.garmin.personal_api import PersonalApiError
from app.models import GarminApiKey, MfaCode
from app.sync_service import get_status, run_sync

router = APIRouter(tags=["sync"])


@router.post("/sync")
def trigger_sync(keep_requests: bool = True):
    """
    Pull from Garmin (60 days into an empty store, else the days since the last
    sync plus an overlap) and write workouts + daily load to InfluxDB.

    keep_requests=false is the athlete's "overwrite my plan changes" answer: the
    new plan ignores the days they asked the coach to change, and those requests
    are deleted. The default keeps them, so no caller loses one by omission.
    """
    try:
        return run_sync(keep_requests=keep_requests)
    except PersonalApiError as e:
        # {code, message, action}: the dashboard's Garmin error modal shows the
        # cause and the suggested action (a new key, or try again later).
        raise HTTPException(status_code=502, detail=e.detail()) from e
    except GarminPullError as e:
        # Clean, user-facing message (auth/MFA/rate-limit/network) -> 502.
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/sync/status")
def sync_status():
    # mfa_pending and sync_phase are live process state, not part of the persisted
    # snapshot: mfa_pending is true only while a sync is blocked waiting for the
    # Garmin email code; sync_phase names the pipeline step a running sync is in.
    return {
        **get_status(),
        "mfa_pending": garmin_client.MFA_STATE["pending"],
        "sync_phase": sync_service.SYNC_PHASE["phase"],
    }


@router.post("/sync/mfa")
def submit_mfa(body: MfaCode):
    """
    Hand the emailed Garmin MFA code to the sync that is currently waiting for it
    (the dashboard's code-entry modal posts here). Writes the code to the file
    _prompt_mfa polls; rejected when no MFA challenge is pending, because
    _prompt_mfa deletes any pre-existing file as stale.
    """
    code = body.code.strip()
    if not re.fullmatch(r"\d{4,8}", code):
        raise HTTPException(status_code=400, detail="The MFA code must be 4-8 digits.")
    if not garmin_client.MFA_STATE["pending"]:
        raise HTTPException(status_code=409, detail="No MFA challenge is pending.")
    garmin_client.MFA_CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    garmin_client.MFA_CODE_FILE.write_text(code, encoding="utf-8")
    return {"submitted": True}


@router.put("/garmin/api-key")
def save_garmin_api_key(body: GarminApiKey):
    """
    Save a new Garmin Personal API key (the error modal's "Save and sync").

    The key is checked with Garmin first and saved only if accepted, into the
    .env file -- the one place credentials live. It is never returned or
    logged. A container started from an env_file has no .env of its own to
    edit; there the athlete updates that file and restarts.
    """
    key = body.key.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.+/=-]{20,400}", key):
        raise HTTPException(status_code=400, detail="That does not look like a Garmin API key.")
    env_file = ROOT / ".env"
    if not env_file.is_file():
        raise HTTPException(
            status_code=409,
            detail=(
                f"No .env file to save into. Set {personal_api.KEY_ENV} in the .env "
                "file the container was started with, then restart it."
            ),
        )
    try:
        personal_api.exchange_key(key)
    except PersonalApiError as e:
        status = 400 if e.code == "key_rejected" else 502
        raise HTTPException(status_code=status, detail=e.detail()) from e
    set_key(str(env_file), personal_api.KEY_ENV, key, quote_mode="never")
    os.environ[personal_api.KEY_ENV] = key
    personal_api.reset_token()
    return {"saved": True}
