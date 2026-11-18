"""
The coach chat's update_athlete_settings tool (app/settings_editor.py): what
it may change, the recalculation after a threshold change, and the approval
guard -- nothing is saved unless the coach's previous reply carried the bold
approval message. Fakes only: the profile lives in a tmp CONFIG_DIR, the
instructions file is a tmp file (tests/conftest.py), Influx is a fake repo.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from app import config, instructions, settings_editor
from app.ai import claude
from app.ai.claude import SETTINGS_APPROVAL_MARKER, SETTINGS_TOOL

PROFILE = {
    "ftp_w": 250.0,
    "run_threshold_pace_sec_km": 321.0,
    "css_sec_100m": 105.0,
    "lthr_bpm": 165.0,
    "weight_kg": 72.0,
    "activity_class": 6,
    "vo2max_decline_threshold": 0.5,
    "birthday": "1988-06-15",
}

APPROVAL = f"{SETTINGS_APPROVAL_MARKER}\n**Bike FTP: 250 W -> 265 W**\nReply yes to save."


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A tmp profile, a fake Influx repo, and a record of every recompute."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    (tmp_path / "athlete.yaml").write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
    config._athlete_yaml.cache_clear()
    monkeypatch.setattr(instructions, "INSTRUCTIONS_FILE", tmp_path / "coach_instructions.txt")

    rec = SimpleNamespace(recomputes=[], mirrors=[], season=[])

    class FakeRepo:
        def write_athlete_profile(self, p):
            rec.mirrors.append(p)

        def close(self):
            pass

    monkeypatch.setattr(settings_editor, "InfluxRepo", FakeRepo)

    from app import sync_service

    def fake_recompute(repo, profile=None, days=None):
        rec.recomputes.append(days)
        return {"considered": 4, "rewritten": 2, "stored_workouts": 40, "load_days": 300}

    monkeypatch.setattr(sync_service, "recompute_stored_metrics", fake_recompute)

    from app.season import service as season_service

    def fake_on_instructions_changed():
        rec.season.append(instructions.get_instructions())
        return None

    monkeypatch.setattr(season_service, "on_instructions_changed", fake_on_instructions_changed)
    yield rec
    config._athlete_yaml.cache_clear()


def _profile() -> dict:
    return config.get_athlete_profile()


# --- update_settings ---------------------------------------------------------
def test_threshold_change_saves_and_recalculates_7_days(env):
    r = settings_editor.update_settings({"ftp_w": 265})
    assert _profile()["ftp_w"] == 265
    assert r["changed"] == {"ftp_w": [250.0, 265.0]}
    assert env.recomputes == [7]
    assert r["recalculated"] == {"days": 7, "considered": 4, "rewritten": 2}
    assert len(env.mirrors) == 1
    # Everything else stays as it was, the birthday and activity class too.
    p = _profile()
    assert (p["birthday"], p["activity_class"], p["lthr_bpm"]) == ("1988-06-15", 6, 165.0)


def test_same_value_saves_nothing_and_skips_the_recalculation(env):
    r = settings_editor.update_settings({"ftp_w": 250})
    assert r["changed"] == {}
    assert env.recomputes == [] and env.mirrors == []
    assert "Nothing changed" in r["note"]


@pytest.mark.parametrize("key", ["birthday", "activity_class", "age", "schedule"])
def test_settings_only_fields_are_refused(env, key):
    with pytest.raises(settings_editor.SettingsEditError, match="Settings"):
        settings_editor.update_settings({key: 5})
    assert _profile()["activity_class"] == 6 and env.recomputes == []


def test_out_of_range_value_is_refused(env):
    with pytest.raises(ValueError):
        settings_editor.update_settings({"vo2max_decline_threshold": 50})
    with pytest.raises(ValueError):
        settings_editor.update_settings({"weight_kg": 0})
    assert _profile()["vo2max_decline_threshold"] == 0.5


def test_instructions_are_replaced_and_the_season_plan_told(env):
    instructions.save_instructions("bricks only on Saturday")
    r = settings_editor.update_settings(
        coach_instructions="bricks only on Saturday\nno swims on Monday"
    )
    assert instructions.get_instructions() == "bricks only on Saturday\nno swims on Monday"
    assert r["instructions_saved"] is True
    assert env.season == ["bricks only on Saturday\nno swims on Monday"]
    assert env.recomputes == []  # instructions alone recompute nothing


def test_nothing_to_change_is_refused(env):
    with pytest.raises(settings_editor.SettingsEditError):
        settings_editor.update_settings(None, None)


# --- the approval guard ------------------------------------------------------
def test_tool_without_approval_is_refused_and_saves_nothing(env):
    text, is_error = claude._run_coach_tool(
        SETTINGS_TOOL, {"profile": {"ftp_w": 265}, "athlete_request": "new FTP"}
    )
    assert is_error and "approved" in text
    assert _profile()["ftp_w"] == 250 and env.recomputes == []


def test_approval_must_be_in_the_last_coach_reply():
    assert claude._approval_asked([{"role": "coach", "text": APPROVAL}, {"role": "user", "text": "yes"}])
    assert not claude._approval_asked([])
    assert not claude._approval_asked([{"role": "user", "text": SETTINGS_APPROVAL_MARKER}])
    # An approval two replies back does not cover a later change.
    assert not claude._approval_asked(
        [
            {"role": "coach", "text": APPROVAL},
            {"role": "user", "text": "yes"},
            {"role": "coach", "text": "Saved."},
            {"role": "user", "text": "and my LTHR is 170"},
        ]
    )


# --- through coach_reply -----------------------------------------------------
def _resp(stop, content):
    return SimpleNamespace(stop_reason=stop, content=content)


def _fake(monkeypatch, script):
    class Msgs:
        calls: list = []

        def create(self, **kw):
            self.calls.append(kw)
            return script.pop(0)

    fc = SimpleNamespace(messages=Msgs())
    monkeypatch.setattr(claude, "_client", lambda: fc)
    return fc


def _settings_call(ftp):
    return _resp(
        "tool_use",
        [
            SimpleNamespace(
                type="tool_use",
                id="s1",
                name=SETTINGS_TOOL,
                input={"profile": {"ftp_w": ftp}, "athlete_request": "new FTP after the test"},
            )
        ],
    )


def test_approved_change_saves_and_flags_settings_updated(env, monkeypatch):
    _fake(monkeypatch, [_settings_call(265), _resp("end_turn", [SimpleNamespace(type="text", text="Saved.")])])
    history = [{"role": "user", "text": "my FTP is 265 now"}, {"role": "coach", "text": APPROVAL}]
    reply, plan_updated, settings_updated = claude.coach_reply("yes", history, "ctx")
    assert (reply, plan_updated, settings_updated) == ("Saved.", False, True)
    assert _profile()["ftp_w"] == 265


def test_unapproved_change_in_coach_reply_is_refused(env, monkeypatch):
    fc = _fake(
        monkeypatch,
        [_settings_call(265), _resp("end_turn", [SimpleNamespace(type="text", text=APPROVAL)])],
    )
    reply, plan_updated, settings_updated = claude.coach_reply("my FTP is 265 now", [], "ctx")
    assert (plan_updated, settings_updated) == (False, False)
    assert _profile()["ftp_w"] == 250
    tool_result = fc.messages.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True


def test_settings_tool_is_offered_with_context_only(monkeypatch):
    fc = _fake(monkeypatch, [_resp("end_turn", [SimpleNamespace(type="text", text="ok")])] * 2)
    claude.coach_reply("hi", [], "ctx", tools=False)
    assert "tools" not in fc.messages.calls[0]
    claude.coach_reply("hi", [], "ctx", tools=True)
    assert SETTINGS_TOOL in [t["name"] for t in fc.messages.calls[1]["tools"]]


def test_tool_schema_cannot_name_the_settings_only_fields():
    tool = next(t for t in claude.COACH_TOOLS if t["name"] == SETTINGS_TOOL)
    fields = tool["input_schema"]["properties"]["profile"]["properties"]
    assert set(fields) == set(settings_editor.COACH_PROFILE_FIELDS)
    assert tool["input_schema"]["properties"]["profile"]["additionalProperties"] is False
    json.dumps(tool)  # serialisable as sent to the API

