"""
Athlete settings changed from outside the Settings modal: the coach chat's
update_athlete_settings tool (app/ai/claude.py), plus the save paths the
Settings routes share with it, so the two can never drift.

The coach may change the six profile numbers in COACH_PROFILE_FIELDS and the
custom coach instructions. The birthday, the activity class and the training
schedule stay Settings-only (the athlete's call), and that is checked here in
code, not only in the tool schema.
"""

from __future__ import annotations

import logging

from app.config import get_athlete_profile, save_athlete_profile
from app.influx.client import InfluxRepo
from app.instructions import get_instructions, save_instructions
from app.models import AthleteProfile, CoachInstructions

log = logging.getLogger("jymalaya.settings")

COACH_PROFILE_FIELDS = (
    "ftp_w",
    "lthr_bpm",
    "run_threshold_pace_sec_km",
    "css_sec_100m",
    "weight_kg",
    "vo2max_decline_threshold",
)

# The Settings "Re-calculate" button's window.
RECALC_DAYS = 7


class SettingsEditError(ValueError):
    """A coach settings change that cannot be applied; the message is for the model."""


def _numeric_fields(profile: dict) -> dict:
    """The fields write_athlete_profile mirrors (numbers only, as floats)."""
    return {k: float(v) for k, v in (profile or {}).items() if isinstance(v, (int, float))}


def save_profile(body: AthleteProfile) -> dict:
    """
    Save the profile (PUT /athlete and the coach tool) and return it as read
    back. mode="json" so the birthday lands in the YAML as an ISO string; the
    derived age is dropped by save_athlete_profile and re-derived on read.
    """
    before = _numeric_fields(get_athlete_profile())
    save_athlete_profile(body.model_dump(mode="json"))
    profile = get_athlete_profile()
    # Best-effort mirror into InfluxDB, written only when a mirrored value
    # changed, so the measurement is a history of threshold changes; the sync
    # never writes it. YAML stays canonical.
    if _numeric_fields(profile) == before:
        return profile
    try:
        repo = InfluxRepo()
        try:
            repo.write_athlete_profile(profile)
        finally:
            repo.close()
    except Exception:  # noqa: BLE001
        pass
    return profile


def save_instructions_and_notify(text: str) -> str | None:
    """
    Save the coach instructions (PUT /coach/instructions and the coach tool).
    An active season plan then reads them again and adjusts from next week on;
    that never fails the save. Returns the season plan's note, if any.
    """
    save_instructions(text)
    # Imported here: the season service imports the prompt builder, which
    # imports app.ai.claude, which imports this module.
    from app.season import service as season_service

    try:
        return season_service.on_instructions_changed()
    except Exception as e:  # noqa: BLE001 -- the save itself already succeeded
        log.warning("Season plan did not follow the new coach instructions: %s", e)
        return None


def _recalculate() -> dict:
    """Recompute the last RECALC_DAYS of TSS, as Settings' Re-calculate does."""
    from app.sync_service import recompute_stored_metrics

    repo = InfluxRepo()
    try:
        r = recompute_stored_metrics(repo, days=RECALC_DAYS)
    finally:
        repo.close()
    return {"days": RECALC_DAYS, "considered": r["considered"], "rewritten": r["rewritten"]}


def update_settings(profile: dict | None = None, coach_instructions: str | None = None) -> dict:
    """
    Apply a coach-approved settings change. `profile` holds only the fields to
    change; `coach_instructions` is the complete new text (empty clears it).
    """
    if not profile and coach_instructions is None:
        raise SettingsEditError("Nothing to change: give profile values or coach_instructions.")

    result: dict = {
        "changed": {},
        "recalculated": None,
        "instructions_saved": False,
        "season_plan_note": None,
    }

    if profile:
        if not isinstance(profile, dict):
            raise SettingsEditError("profile must be an object of setting: value.")
        unknown = sorted(set(profile) - set(COACH_PROFILE_FIELDS))
        if unknown:
            raise SettingsEditError(
                f"{', '.join(unknown)} cannot be changed from the chat. The coach can "
                f"change {', '.join(COACH_PROFILE_FIELDS)}; the birthday, activity "
                "class and training schedule are changed in Settings."
            )
        current = get_athlete_profile()
        merged = {k: v for k, v in current.items() if k != "age"}
        merged.update(profile)
        body = AthleteProfile.model_validate(merged)  # ValidationError on a bad value
        new = body.model_dump()
        changed = {
            k: [current.get(k), new[k]]
            for k in profile
            if current.get(k) is None or float(current[k]) != float(new[k])
        }
        if changed:
            save_profile(body)
            result["changed"].update(changed)
            try:
                result["recalculated"] = _recalculate()
            except Exception as e:  # noqa: BLE001 -- the save stands
                log.warning("Recalculation after a coach settings change failed: %s", e)
                result["recalculated"] = {"error": f"Saved, but the TSS recalculation failed: {e}"}

    if coach_instructions is not None:
        text = CoachInstructions(text=coach_instructions.strip()).text
        old = get_instructions()
        if text != old:
            result["season_plan_note"] = save_instructions_and_notify(text)
            result["instructions_saved"] = True
            result["changed"]["coach_instructions"] = [old, text]

    if not result["changed"]:
        result["note"] = "Nothing changed: every value was already saved."
    else:
        result["note"] = (
            "Saved. The 7-day plan picks this up at the next Sync or Re-sync plan."
        )
    return result
