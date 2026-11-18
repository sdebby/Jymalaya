"""The chat's suggestion chips: the coach ends its reply with follow-up questions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from app.ai import claude
from app.routers import coach as coach_router

MARK = claude.SUGGESTIONS_MARKER


def test_block_is_cut_off_and_parsed():
    reply, found = claude.split_suggestions(
        f'Run easy tomorrow.\n\n{MARK}\n["Why easy?", "Can I swim instead?", "How long?"]'
    )
    assert reply == "Run easy tomorrow."
    assert found == ["Why easy?", "Can I swim instead?", "How long?"]


def test_reply_without_a_block_is_untouched():
    assert claude.split_suggestions("Run easy tomorrow.") == ("Run easy tomorrow.", [])


@pytest.mark.parametrize("tail", ["", "not json", '{"a": 1}', "[1, 2", '"just a string"'])
def test_bad_block_gives_no_suggestions_and_still_leaves_the_reply(tail):
    assert claude.split_suggestions(f"Answer.\n{MARK}\n{tail}") == ("Answer.", [])


def test_only_three_short_distinct_strings_are_kept():
    long = "x" * (claude.MAX_SUGGESTION_CHARS + 1)
    reply, found = claude.split_suggestions(
        f'Answer.\n{MARK}\n```json\n["a?", 7, "a?", "{long}", "  b   ?", "c?", "d?"]\n```'
    )
    assert reply == "Answer."
    assert found == ["a?", "b ?", "c?"]


def test_the_last_marker_wins():
    reply, found = claude.split_suggestions(f'One {MARK} two\n{MARK}\n["q?"]')
    assert reply == f"One {MARK} two"
    assert found == ["q?"]


@pytest.mark.parametrize("include_context", [True, False])
def test_route_sends_the_rule_and_returns_the_suggestions(monkeypatch, include_context):
    seen = {}

    def fake_reply(message, history, context, *, tools=True):
        seen.update(context=context, tools=tools)
        return f'Do the long run.\n{MARK}\n["How far?", "What pace?", "Which day?"]', False, False

    monkeypatch.setattr(coach_router, "coach_reply", fake_reply)
    monkeypatch.setattr(coach_router, "build_coach_context", lambda: "ATHLETE CONTEXT")
    monkeypatch.setattr(coach_router, "build_general_coach_context", lambda: "GENERAL CONTEXT")

    client = TestClient(main.app, base_url="http://testserver/api")
    res = client.post(
        "/coach", json={"message": "hi", "history": [], "include_context": include_context}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "Do the long run."
    assert body["suggestions"] == ["How far?", "What pace?", "Which day?"]
    assert seen["tools"] is include_context
    assert seen["context"].startswith("ATHLETE CONTEXT" if include_context else "GENERAL CONTEXT")
    assert seen["context"].endswith(claude.COACH_SUGGESTIONS_RULE)
    assert MARK in claude.COACH_SUGGESTIONS_RULE
