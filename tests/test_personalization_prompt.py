"""
The plan system prompt's rule 9 is substituted per race distance.

No network: these only read the prompt files and the pure assembly helpers.
"""

from __future__ import annotations

import re
import typing

import pytest

from app.ai import claude
from app.models import EventType

GOAL_TYPES = typing.get_args(EventType)


def test_every_goal_type_maps_to_an_existing_file():
    for goal_type in GOAL_TYPES:
        assert goal_type in claude.PERSONALIZATION_FILES, goal_type
        path = claude.PROMPTS_DIR / claude.PERSONALIZATION_FILES[goal_type]
        assert path.exists(), path


def test_no_stray_mappings():
    assert set(claude.PERSONALIZATION_FILES) == set(GOAL_TYPES)


RULE_9_LINE = (
    "9. Apply the periodization phase for the athlete's goal distance, "
    "selected from weeks_until_race:"
)


def test_system_prompt_has_exactly_one_token():
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    assert text.count(claude.PERSONALIZATION_TOKEN) == 1
    assert RULE_9_LINE in text


# A phase bullet opens with the weeks-to-race band it applies to, e.g.
# "- > 10 weeks: base." or "- 2-6 weeks: peak.".
PHASE_BULLET = re.compile(r"^\s*-\s+[<>\d].*weeks?\b.*:", re.MULTILINE)


@pytest.mark.parametrize("goal_type", GOAL_TYPES)
def test_every_personalization_file_carries_a_phase_table(goal_type):
    # The file is rule 9's whole body, so a stub one leaves the plan with no
    # periodization at all -- which the existence check above would not catch.
    text = claude._personalization_text(goal_type)
    assert len(PHASE_BULLET.findall(text)) >= 4, text
    assert "taper" in text.lower()


def test_distance_specific_file_is_chosen():
    assert "70.3 intensity" in claude._personalization_text("70.3")
    assert "Ironman is an aerobic" in claude._personalization_text("140.6")
    assert "VO2-max event" in claude._personalization_text("5K")

    system = claude._build_plan_system({"primary_goal": {"type": "5K"}})
    assert RULE_9_LINE in system
    assert system.count(RULE_9_LINE) == 1


def test_unknown_and_missing_type_fall_back_to_custom():
    custom = claude._personalization_text("custom")
    assert custom.strip().startswith("The goal")
    assert claude._personalization_text(None) == custom
    assert claude._personalization_text("duathlon") == custom


def test_missing_file_falls_back_to_custom(monkeypatch, caplog):
    monkeypatch.setitem(claude.PERSONALIZATION_FILES, "5K", "Nope_Personalization.txt")
    with caplog.at_level("WARNING", logger="jymalaya.claude"):
        text = claude._personalization_text("5K")
    assert text == claude._personalization_text("custom")
    assert "Nope_Personalization.txt" in caplog.text


def test_build_plan_system_substitutes_the_token():
    system = claude._build_plan_system({"primary_goal": {"type": "5K"}})
    assert claude.PERSONALIZATION_TOKEN not in system
    assert RULE_9_LINE in system
    assert "VO2 is the priority" in system
    # The JSON output template still follows the prompt.
    assert "periodization_phase" in system


def test_build_plan_system_without_a_goal():
    system = claude._build_plan_system({"primary_goal": None})
    assert claude.PERSONALIZATION_TOKEN not in system
    assert "no standard race distance" in system
