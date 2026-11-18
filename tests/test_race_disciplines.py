"""
The goal picks the disciplines; the schedule picks only the days.

No network: these read the models, the prompt files and the pure helpers.
"""

from __future__ import annotations

import typing
from datetime import date

import pytest

from app.ai import claude
from app.ai.prompt_builder import _goal_block
from app.models import RACE_DISCIPLINES, Discipline, EventType, race_disciplines

GOAL_TYPES = typing.get_args(EventType)
DISCIPLINE_VALUES = set(typing.get_args(Discipline))

RUN_ONLY = ("marathon", "half-marathon", "15K", "10K", "5K")
TRIATHLON = ("sprint", "olympic", "70.3", "140.6")


# --- the mapping ------------------------------------------------------------
def test_every_goal_type_is_mapped():
    """Mirrors test_no_stray_mappings: a new EventType breaks here first."""
    assert set(RACE_DISCIPLINES) == set(GOAL_TYPES)


def test_every_value_is_a_real_discipline():
    for goal_type, disciplines in RACE_DISCIPLINES.items():
        if disciplines is None:
            continue
        assert set(disciplines) <= DISCIPLINE_VALUES, goal_type


def test_strength_is_never_a_race_discipline():
    """Strength is support work; rule 4 keeps it on any day that asks for it."""
    for disciplines in RACE_DISCIPLINES.values():
        assert "strength" not in (disciplines or ())


@pytest.mark.parametrize("goal_type", RUN_ONLY)
def test_run_races_are_run_only(goal_type):
    assert race_disciplines(goal_type) == ["run"]


@pytest.mark.parametrize("goal_type", TRIATHLON)
def test_triathlons_keep_all_three(goal_type):
    assert race_disciplines(goal_type) == ["swim", "bike", "run"]


@pytest.mark.parametrize("goal_type", ["custom", None, "", "duathlon"])
def test_unknown_disciplines_do_not_restrict(goal_type):
    """None means "no restriction" -- rule 4 then falls back to the schedule."""
    assert race_disciplines(goal_type) is None


def test_goal_block_carries_the_race_disciplines():
    today = date(2026, 9, 20)
    goal = {"name": "Tel Aviv", "type": "marathon", "race_date": "2026-11-01"}
    assert _goal_block(goal, today)["race_disciplines"] == ["run"]
    custom = dict(goal, type="custom")
    assert _goal_block(custom, today)["race_disciplines"] is None


# --- the prompts ------------------------------------------------------------
def _run_file_text(goal_type: str) -> str:
    return claude._personalization_text(goal_type)


@pytest.mark.parametrize("goal_type", RUN_ONLY)
def test_run_files_no_longer_defer_to_the_schedule(goal_type):
    """
    The old wording let the schedule reopen swim and bike, and DEFAULT_SCHEDULE
    asks for both -- so a marathon plan got a swim every week. This is the test
    that stops it being reintroduced.
    """
    text = _run_file_text(goal_type)
    assert "unless the schedule explicitly asks" not in text
    assert "Run-only goal: plan runs and nothing else." in text
    assert "Run days per week:" in text
    assert "No bricks:" in text


@pytest.mark.parametrize("goal_type", TRIATHLON)
def test_triathlon_files_still_program_bricks(goal_type):
    """race_disciplines is all three here, so nothing may suppress them."""
    assert "brick" in _run_file_text(goal_type).lower()


def test_rule_4_is_driven_by_the_goal():
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    assert "4. The GOAL picks the disciplines; the SCHEDULE picks only the days." in text
    assert "primary_goal.race_disciplines" in text
    # The old permissive sentence must not survive anywhere.
    assert "Respect preferred_disciplines per day" not in text


def test_custom_file_explains_the_null_branch():
    assert "race_disciplines is null" in _run_file_text("custom")
