"""
How a plan session's structure is written (rule 10). The 2026-09-30 VO2max run
came back as "Interval 1" with notes of its own, four "Recovery"/"Interval"
pairs whose notes said "Same target as Interval 1", no fifth recovery, and a
"Cooldown extra" after the cooldown. The dashboard could not fold that into
5 x (Interval, Recovery), and the watch got a step after the cooldown. Fakes
only: this reads the prompt text.
"""

from __future__ import annotations

from app.ai import claude


def _rule_10() -> str:
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return text[text.index("10. Each workout must include") : text.index("\n11. ")]


def test_nothing_comes_after_the_cooldown():
    rule = _rule_10()
    assert "cooldown LAST" in rule
    assert "nothing comes after it" in rule
    assert '"Cooldown extra"' in rule
    assert "never append a segment" in rule


def test_every_rep_keeps_its_recovery():
    rule = _rule_10()
    assert "N repetitions of (work, recovery)" in rule
    assert "the work first" in rule
    assert "The last rep keeps its recovery too" in rule


def test_reps_are_identical_and_never_point_at_each_other():
    rule = _rule_10()
    assert "word for word the" in rule
    assert "never \"Same target as Interval 1\"" in rule
