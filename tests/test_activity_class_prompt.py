"""
Prompt rule 34: the activity class caps the quality sessions of the 7-day
plan. Prompt only, never enforced in code; appended after rule 33 so no rule
is renumbered. The coach context points at the same bands.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = (ROOT / "prompts" / "recommendation_system.txt").read_text(encoding="utf-8")


def _rule(n: int) -> str:
    m = re.search(rf"^{n}\. (.*?)(?=^\d+\. |^Respond ONLY|^[A-Z][^\n]*:\n\d)", PROMPT, re.S | re.M)
    assert m, f"rule {n} missing"
    return m.group(0)


def test_rule_34_is_appended_last():
    numbers = [int(n) for n in re.findall(r"^(\d+)\. ", PROMPT, re.M)]
    assert numbers == list(range(1, 35))


def test_rule_34_names_the_bands_and_what_outranks_it():
    rule = _rule(34)
    assert "athlete_profile.activity_class" in rule
    assert "0-3: at most one quality session" in rule
    assert "4-6: at most two quality sessions" in rule
    assert "7-10: up to three quality sessions" in rule
    assert "ceilings, not targets" in rule
    assert "Rules 1-6, 17-20, 24, 31 and 32 outrank this rule" in rule
    assert "athlete_custom_instructions may override it" in rule
    # It shapes delivery, never the TSS.
    assert "never how much" in rule


def test_the_coach_context_points_at_the_bands():
    src = (ROOT / "app" / "ai" / "prompt_builder.py").read_text(encoding="utf-8")
    assert "caps the quality" in src and "0-3: one, 4-6: two" in src
