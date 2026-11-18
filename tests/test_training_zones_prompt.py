"""
The zone bands and strict pace rules in prompts/training_zones.txt, shared by
the plan, analysis and coach prompts.

The bug they fix: with no zones in any prompt, the coach took a percent OF the
athlete's threshold pace and quoted Z2 at 4:20/km against a 5:21/km threshold --
faster than threshold, for an easy zone. So pace bands are written as factors
on threshold seconds, and every easy-zone factor is above 1.00.

No network: these only read the prompt files and the pure assembly helpers.
"""

from __future__ import annotations

import json
import re

import pytest

from app.ai import claude
from app.metrics.analysis import POWER_ZONE_BANDS

ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")
BAND_LABELS = ("Heart rate", "Bike power", "Run pace:", "Swim pace:")


def _bands(label: str) -> dict[str, list[float]]:
    """
    The numbers on each zone of the band line under `label`, e.g. for run pace
    {"Z1": [1.29], "Z2": [1.14, 1.29], ..., "Z5": [1.0]}.
    """
    lines = claude.training_zones_text().splitlines()
    i = next(i for i, line in enumerate(lines) if line.startswith(label))
    out = {}
    for part in lines[i + 1].split("|"):
        zone, text = part.strip().split(" ", 1)
        out[zone] = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    return out


def _mmss(sec: float) -> str:
    sec = int(sec + 0.5)
    return f"{sec // 60}:{sec % 60:02d}"


# --- wiring -----------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [claude.SYSTEM_PROMPT_PATH, claude.ANALYSIS_PROMPT_PATH],
    ids=["plan", "analysis"],
)
def test_each_prompt_has_exactly_one_zones_token(path):
    assert path.read_text(encoding="utf-8").count(claude.ZONES_TOKEN) == 1


BUILDERS = {
    "plan": (
        lambda: claude._build_plan_system({"primary_goal": {"type": "olympic"}}),
        claude.PLAN_FORMAT_PATH,
    ),
    "analysis": (claude._build_analysis_system, claude.ANALYSIS_FORMAT_PATH),
}


@pytest.mark.parametrize("name", BUILDERS)
def test_built_prompts_carry_the_zones(name):
    build, format_path = BUILDERS[name]
    system = build()
    assert claude.ZONES_TOKEN not in system
    assert claude.training_zones_text() in system
    # The JSON output template still closes the prompt.
    assert system.endswith(format_path.read_text(encoding="utf-8"))


def test_plan_template_puts_the_working_before_the_totals():
    """
    The model fills the template top to bottom. With duration_min ahead of the
    notes it committed to a typical race time first, then wrote a calculation
    that contradicted it -- a 1500 m swim at a 2:40 CSS set to 25 min.
    """
    template = json.loads(claude.PLAN_FORMAT_PATH.read_text(encoding="utf-8"))
    session = template["days"][0]["sessions"][0]
    keys = list(session)
    assert keys.index("structure") < keys.index("duration_min")
    assert keys.index("structure") < keys.index("estimated_tss")
    segment = list(session["structure"][0])
    assert segment.index("notes") < segment.index("duration_min")


# --- the rules themselves ---------------------------------------------------
def test_zones_state_the_pace_direction():
    assert "a LARGER min:sec is SLOWER" in claude.training_zones_text()


def test_zones_fix_the_units_the_athlete_reads():
    """Paces in min:sec, never seconds; a ride's speed in km/h, never a pace."""
    text = " ".join(claude.training_zones_text().split())
    assert "the athlete never sees a pace in seconds" in text
    assert "Running pace is min:sec per km" in text
    assert "cycling speed is km/h" in text


def test_plan_prompt_examples_are_in_min_sec():
    """The rule 14 worked example is what the model copies into segment notes."""
    text = claude.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    for unit in ("s/km", "sec/km", "s/100"):
        assert unit not in text, unit
    assert "5:00/km x 1.00-1.06 = 5:00-5:18/km" in " ".join(text.split())


@pytest.mark.parametrize("label", BAND_LABELS)
def test_every_band_line_lists_the_five_zones_in_order(label):
    assert tuple(_bands(label)) == ZONES


@pytest.mark.parametrize("label", ["Run pace:", "Swim pace:"])
def test_easy_pace_factors_are_all_slower_than_threshold(label):
    """The regression itself: no Z1-Z3 factor may put a pace ahead of threshold."""
    bands = _bands(label)
    for zone in ("Z1", "Z2", "Z3"):
        assert all(f > 1.0 for f in bands[zone]), (zone, bands[zone])


@pytest.mark.parametrize("label", ["Run pace:", "Swim pace:"])
def test_pace_bands_run_from_slowest_to_fastest_without_gaps(label):
    b = _bands(label)
    # Adjacent zones share their boundary: Z1's floor is Z2's top, and so on.
    assert b["Z1"][0] == b["Z2"][1]
    assert b["Z2"][0] == b["Z3"][1]
    assert b["Z3"][0] == b["Z4"][1]
    assert b["Z4"][0] == b["Z5"][0]
    boundaries = [b[z][0] for z in ("Z1", "Z2", "Z3", "Z4")]
    assert boundaries == sorted(set(boundaries), reverse=True)


def test_run_z4_starts_at_threshold():
    assert _bands("Run pace:")["Z4"][0] == 1.0


def test_the_bug_report_athlete_gets_an_easy_z2():
    """Threshold 5:21/km (321 s): Z2 is 6:06-6:54, where the coach said 4:20-4:35."""
    fast, slow = _bands("Run pace:")["Z2"]
    assert (_mmss(321 * fast), _mmss(321 * slow)) == ("6:06", "6:54")


def test_hr_bands_are_the_athletes_garmin_zones():
    """%LTHR 65/80/89/96/100, from the athlete's Garmin zones (assets/HRZone.pptx)."""
    assert _bands("Heart rate") == {
        "Z1": [65, 80],
        "Z2": [80, 89],
        "Z3": [89, 96],
        "Z4": [96, 100],
        "Z5": [100],
    }


def test_bike_bands_match_the_analysis_power_split():
    """
    The prompt's integer %FTP labels are the bands the post-workout analysis
    splits power by, so a zone means the same watts in both places.
    """
    b = _bands("Bike power")
    starts = [round(low * 100) for _, low, _ in POWER_ZONE_BANDS[1:]]
    assert [b[z][0] for z in ("Z2", "Z3", "Z4", "Z5")] == starts
    # Each label ends one percent below where the next zone starts.
    assert b["Z1"][0] == starts[0] - 1
    assert [b[z][1] for z in ("Z2", "Z3", "Z4")] == [s - 1 for s in starts[1:]]
