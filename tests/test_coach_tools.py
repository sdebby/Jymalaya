"""
Coach tool-use loop: coach_reply drives the Anthropic tools API against
app.plan_editor. No real Claude involved -- _client() is monkeypatched to a
FakeClient with scripted responses (style follows tests/test_mfa.py).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import plan_editor
from app.ai import claude
from main import app

# base_url carries the /api prefix the routers are mounted under, so the paths
# below stay the bare route paths.
api = TestClient(app, base_url="http://testserver/api")


# --- fakes -------------------------------------------------------------------

def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def _resp(stop_reason, content):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # kwargs of every create()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.script:
            return self.script.pop(0)
        # Keep asking for tools forever (used by the iteration-cap test) unless
        # the caller forbids them.
        if kwargs.get("tool_choice") == {"type": "none"}:
            return _resp("end_turn", [_text_block("gave up on tools")])
        return _resp(
            "tool_use",
            [_tool_block("loop", "remove_plan_session", {"date": "2026-07-10", "session_index": 0})],
        )


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def _fake(monkeypatch, script) -> FakeClient:
    fc = FakeClient(script)
    monkeypatch.setattr(claude, "_client", lambda: fc)
    return fc


# --- fixtures ----------------------------------------------------------------

def _sample_plan() -> dict:
    return {
        "days": [
            {
                "date": "2026-07-10",
                "day_name": "Friday",
                "is_rest_day": False,
                "sessions": [
                    {"discipline": "run", "title": "Easy run", "duration_min": 45, "estimated_tss": 40}
                ],
            },
            {"date": "2026-07-11", "day_name": "Saturday", "is_rest_day": True, "sessions": []},
        ],
        "_meta": {"plan_id": "abc"},
    }


@pytest.fixture()
def plan_file(tmp_path, monkeypatch):
    f = tmp_path / "latest_recommendation.json"
    f.write_text(json.dumps(_sample_plan()), encoding="utf-8")
    monkeypatch.setattr(plan_editor, "LATEST_FILE", f)
    return f


# --- coach_reply loop --------------------------------------------------------

def test_happy_path_tool_round_then_text(plan_file, monkeypatch):
    new_session = {"discipline": "run", "title": "Short run", "duration_min": 30, "estimated_tss": 25}
    fc = _fake(
        monkeypatch,
        [
            _resp("tool_use", [
                _text_block("Let me update that."),
                _tool_block("tu_1", "update_plan_session",
                            {"date": "2026-07-10", "session_index": 0, "session": new_session}),
            ]),
            _resp("end_turn", [_text_block("Done — Friday's run is now 30 minutes.")]),
        ],
    )

    reply, plan_updated, _ = claude.coach_reply("make friday's run 30 min", [], "ctx")

    assert reply == "Done — Friday's run is now 30 minutes."
    assert plan_updated is True
    saved = json.loads(plan_file.read_text(encoding="utf-8"))
    assert saved["days"][0]["sessions"][0]["duration_min"] == 30
    assert saved["_meta"]["modified_by"] == "coach"

    # Second create call must carry the assistant turn + ONE tool_result user msg.
    msgs = fc.messages.calls[1]["messages"]
    assert msgs[-2]["role"] == "assistant"
    results = msgs[-1]
    assert results["role"] == "user"
    assert len(results["content"]) == 1
    tr = results["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["tool_use_id"] == "tu_1"
    assert tr["is_error"] is False


def test_tool_error_reports_is_error_and_no_update(plan_file, monkeypatch):
    fc = _fake(
        monkeypatch,
        [
            _resp("tool_use", [
                _tool_block("tu_1", "remove_plan_session", {"date": "2026-07-20", "session_index": 0}),
            ]),
            _resp("end_turn", [_text_block("Sorry, that date isn't in the plan.")]),
        ],
    )

    reply, plan_updated, _ = claude.coach_reply("remove the workout on the 20th", [], "ctx")

    assert plan_updated is False
    assert reply == "Sorry, that date isn't in the plan."
    tr = fc.messages.calls[1]["messages"][-1]["content"][0]
    assert tr["is_error"] is True
    assert "2026-07-10" in tr["content"]  # error lists valid dates
    # Plan untouched.
    assert len(json.loads(plan_file.read_text(encoding="utf-8"))["days"][0]["sessions"]) == 1


def test_no_tools_needed_single_call(plan_file, monkeypatch):
    fc = _fake(monkeypatch, [_resp("end_turn", [_text_block("Zone 2, keep it easy.")])])
    reply, plan_updated, _ = claude.coach_reply("why is friday easy?", [], "ctx")
    assert (reply, plan_updated) == ("Zone 2, keep it easy.", False)
    assert len(fc.messages.calls) == 1


def test_iteration_cap_forces_text(plan_file, monkeypatch):
    # Empty script: FakeMessages keeps requesting tools until tool_choice=none.
    fc = _fake(monkeypatch, [])
    reply, plan_updated, _ = claude.coach_reply("keep editing forever", [], "ctx")

    assert reply == "gave up on tools"
    assert len(fc.messages.calls) == claude.MAX_TOOL_ROUNDS + 1
    assert fc.messages.calls[-1]["tool_choice"] == {"type": "none"}
    assert all("tool_choice" not in c for c in fc.messages.calls[:-1])
    # The looped remove calls really ran (first one succeeds).
    assert plan_updated is True
    # Every round, the forced final one included, sends the same cached system
    # block, so rounds 2+ read tools + system from the prompt cache.
    for call in fc.messages.calls:
        assert call["system"] == [
            {"type": "text", "text": "ctx", "cache_control": {"type": "ephemeral"}}
        ]
        assert call["cache_control"] == {"type": "ephemeral"}


def test_coach_runs_on_sonnet_5_5_without_up_front_thinking(plan_file, monkeypatch):
    monkeypatch.setattr(claude, "COACH_MODEL", "claude-sonnet-5-5")
    fc = _fake(monkeypatch, [_resp("end_turn", [_text_block("ok")])])
    claude.coach_reply("hi", [], "ctx")
    call = fc.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    # Sonnet 5.5 returns a 400 for {"type": "disabled"}.
    assert call["thinking"] == {"type": "between_tools"}


def test_coach_effort_is_capped_at_high_for_between_tools(plan_file, monkeypatch):
    # between_tools returns a 400 at xhigh / max.
    monkeypatch.setattr(claude, "COACH_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(claude, "EFFORT", "max")
    fc = _fake(monkeypatch, [_resp("end_turn", [_text_block("ok")])])
    claude.coach_reply("hi", [], "ctx")
    assert fc.messages.calls[0]["output_config"] == {"effort": "high"}


def test_coach_on_an_older_model_still_disables_thinking(plan_file, monkeypatch):
    monkeypatch.setattr(claude, "COACH_MODEL", "claude-sonnet-4-6")
    fc = _fake(monkeypatch, [_resp("end_turn", [_text_block("ok")])])
    claude.coach_reply("hi", [], "ctx")
    assert fc.messages.calls[0]["thinking"] == {"type": "disabled"}


def test_thinking_block_is_replayed_and_text_read_by_type(plan_file, monkeypatch):
    # Sonnet 5.5 can open a response with a thinking block (progress notes
    # between tool calls); it must go back unchanged and never be read as text.
    think = SimpleNamespace(type="thinking", thinking="", signature="sig")
    fc = _fake(
        monkeypatch,
        [
            _resp(
                "tool_use",
                [
                    think,
                    _tool_block(
                        "t1", "remove_plan_session", {"date": "2026-07-10", "session_index": 0}
                    ),
                ],
            ),
            _resp("end_turn", [think, _text_block("Removed it.")]),
        ],
    )
    reply, _, _ = claude.coach_reply("drop the swim", [], "ctx")
    assert reply == "Removed it."
    assistant = fc.messages.calls[1]["messages"][-2]
    assert assistant["role"] == "assistant" and assistant["content"][0] is think


def test_history_mapping_drops_leading_greeting(plan_file, monkeypatch):
    fc = _fake(monkeypatch, [_resp("end_turn", [_text_block("ok")])])
    history = [
        {"role": "coach", "text": "hi, I'm your coach"},
        {"role": "user", "text": "hello"},
        {"role": "coach", "text": "what's up?"},
    ]
    claude.coach_reply("question", history, "ctx")
    msgs = fc.messages.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"] == "question"


# --- endpoint ----------------------------------------------------------------

def test_endpoint_returns_plan_updated_flag(monkeypatch):
    from app.routers import coach as coach_router

    monkeypatch.setattr(coach_router, "build_coach_context", lambda: "ctx")
    monkeypatch.setattr(coach_router, "coach_reply", lambda m, h, c, **kw: ("hi", True, False))
    res = api.post("/coach", json={"message": "hey", "history": []})
    assert res.status_code == 200
    assert res.json() == {
        "reply": "hi",
        "plan_updated": True,
        "settings_updated": False,
        "suggestions": [],
    }


def test_endpoint_claude_error_is_503(monkeypatch):
    from app.routers import coach as coach_router

    def boom(m, h, c, **kw):
        raise claude.ClaudeError("no key")

    monkeypatch.setattr(coach_router, "build_coach_context", lambda: "ctx")
    monkeypatch.setattr(coach_router, "coach_reply", boom)
    res = api.post("/coach", json={"message": "hey", "history": []})
    assert res.status_code == 503
    assert res.json()["detail"] == "no key"
