"""
The season plan's phase table is the prompt files' phase lines, exactly.

Claude picks a week's phase from rule 9's per-distance file; the season plan
picks it from app/season/config.PHASE_WEEKS_OUT. If the two ever drift, the
dashboard and the 7-day plan name different phases for the same week -- so
this reads every prompts/<Distance>_Personalization.txt and requires equality.
"""

from __future__ import annotations

import re
import typing

import pytest

from app.ai import claude
from app.models import EventType
from app.season import config as cfg

TYPES = typing.get_args(EventType)

BASE = re.compile(r"-\s*>\s*(\d+)\s*weeks?:\s*base")
BUILD = re.compile(r"-\s*(\d+)\s*-\s*(\d+)\s*weeks?:\s*build")
PEAK = re.compile(r"-\s*(\d+)\s*-\s*(\d+)\s*weeks?:\s*peak")
TAPER = re.compile(r"-\s*<\s*(\d+)\s*weeks?:\s*taper")


def _file_table(event_type: str) -> dict[str, int]:
    name = claude.PERSONALIZATION_FILES[event_type]
    text = (claude.PROMPTS_DIR / name).read_text(encoding="utf-8")
    matches = {k: rx.findall(text) for k, rx in (("base", BASE), ("build", BUILD), ("peak", PEAK), ("taper", TAPER))}
    for key, found in matches.items():
        assert len(found) == 1, f"{name}: expected one {key} line, found {found}"
    base_above = int(matches["base"][0])
    build_lo, build_hi = map(int, matches["build"][0])
    peak_lo, peak_hi = map(int, matches["peak"][0])
    taper_lt = int(matches["taper"][0])
    # The lines must tile the runway with no gap: each phase starts where the
    # next one ends.
    assert build_hi == base_above, name
    assert peak_hi == build_lo, name
    assert taper_lt == peak_lo, name
    return {"taper": taper_lt, "peak": peak_hi, "build": build_hi}


@pytest.mark.parametrize("event_type", TYPES)
def test_code_table_matches_the_prompt_file(event_type):
    assert cfg.PHASE_WEEKS_OUT[event_type] == _file_table(event_type)


def test_every_distance_has_a_table_row_and_peak_ctl():
    assert set(cfg.PHASE_WEEKS_OUT) == set(TYPES)
    assert set(cfg.PEAK_CTL) == set(TYPES)


def test_every_taper_length_has_factors():
    for row in cfg.PHASE_WEEKS_OUT.values():
        factors = cfg.TAPER_FACTORS[row["taper"]]
        assert len(factors) == row["taper"]
        assert factors == sorted(factors, reverse=True)
