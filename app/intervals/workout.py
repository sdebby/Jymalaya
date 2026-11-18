"""
A planned session as an intervals.icu workout (the dashboard's Send to device).

intervals.icu reads a workout from plain text in the event description: one
`- <cue> <duration> <target>` line per step, where the cue is the step's name on
the watch. Everything here is pure -- no I/O -- so the translation can be pinned
by tests.

Targets come from the bands every Claude prompt uses (prompts/training_zones.txt),
with two changes a device needs:
  - every band is closed. A watch cannot hold "Z5 = faster than threshold", so
    each open end gets a bound (marked below).
  - bike and swim targets are absolute (watts, pace per 100 m) from the Jymalaya
    profile, so they do not depend on the zones set up in intervals.icu. Run
    steps are % LTHR, the athlete's choice: intervals.icu turns that into bpm
    with the LTHR saved in intervals.icu, which must match the one in Settings.

Claude writes interval sets unrolled ("Interval 1", "Recovery 1", "Interval
2", ...); they go to the watch folded into an intervals.icu repeat ("5x" over
the steps of one rep), by the rules the modal draws them with (groupStructure in
frontend/src/derive.ts). A repeat needs two full reps. A set whose last rep has
no closing recovery is sent as "(N-1)x" plus that last hard step, so the watch
gets exactly the plan's time.

Every step line ends in an explicit `intensity=` tag -- warmup, active, rest or
cooldown -- which intervals.icu passes to Garmin as the step type (Warm Up, the
sport's own name, Rest, Cool Down). Without it intervals.icu guesses, and the
guess sent every step of a threshold run to Garmin as "Run". The tag comes from
the segment's name first, then from its zone and its place in the session
(_intensity). Every rep of a set gets the same tag, so repeats still fold.
"""

from __future__ import annotations

import re

SPORT_TYPE = {"swim": "Swim", "bike": "Ride", "run": "Run", "strength": "WeightTraining"}

# Neither the plan nor Garmin needs a time of day; each session of a day gets
# its own hour so two sessions keep their order on the calendar.
DAY_START_HOUR = 7

# % of LTHR. The prompt's Z5 is "above 100%"; 106 closes it.
RUN_HR_BANDS = {"Z1": (65, 80), "Z2": (80, 89), "Z3": (89, 96), "Z4": (96, 100), "Z5": (100, 106)}

# % of FTP. The prompt's Z1 is "up to 55%"; 45 floors it, since a 0 W target is
# no target at all. The same floor raises a "0-104" target_power_w.
BIKE_FLOOR_PCT = 45
BIKE_POWER_BANDS = {
    "Z1": (BIKE_FLOOR_PCT, 55),
    "Z2": (56, 75),
    "Z3": (76, 90),
    "Z4": (91, 105),
    "Z5": (106, 120),
}

# css_sec_100m multiplied by these, faster end first. The prompt's Z1 is "over
# 1.21" and its Z5 "under 0.98"; 1.35 and 0.90 close them.
SWIM_PACE_BANDS = {
    "Z1": (1.21, 1.35),
    "Z2": (1.10, 1.21),
    "Z3": (1.03, 1.10),
    "Z4": (0.98, 1.03),
    "Z5": (0.90, 0.98),
}

_ZONE_RE = re.compile(r"^\s*z([1-5])\s*(?:[-–—]\s*z?([1-5]))?\s*$", re.IGNORECASE)
_WATTS_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*w?\s*(?:[-–—]\s*(\d+(?:\.\d+)?)\s*w?)?\s*$", re.IGNORECASE
)

# Words intervals.icu could read as part of a target, kept out of a step's cue.
_CUE_STOPWORDS = {
    "pace", "hr", "lthr", "bpm", "ramp", "rpm", "freeride", "mmp", "mtr", "km", "mi",
}


def external_id(date: str, session_index: int) -> str:
    """The key intervals.icu upserts on: one calendar entry per plan slot."""
    return f"jymalaya-{date}-{session_index}"


def session_to_event(date: str, session_index: int, session: dict, profile: dict) -> dict:
    """The intervals.icu calendar event for one plan session."""
    discipline = str(session.get("discipline") or "").lower()
    sport = SPORT_TYPE.get(discipline)
    if sport is None:
        raise ValueError(f"A {discipline or 'session with no discipline'} cannot be sent to the watch.")
    name = str(session.get("title") or "").strip() or f"{discipline.capitalize()} session"
    hour = min(DAY_START_HOUR + session_index, 23)
    return {
        "category": "WORKOUT",
        "type": sport,
        "name": name,
        "start_date_local": f"{date}T{hour:02d}:00:00",
        "description": workout_text(session, profile),
        "external_id": external_id(date, session_index),
    }


def workout_text(session: dict, profile: dict) -> str:
    """
    The event description, kept short for the watch: a "Run intervals" heading,
    one step line per structure segment, then "Benefit - ...". The session's own
    notes are deliberately left out (they carry the pace working). Raises
    ValueError when there is no duration anywhere to build a step from.
    """
    discipline = str(session.get("discipline") or "").lower()
    # Only the segments that become steps, so "first" and "last" in _intensity
    # mean the first and last step the watch gets.
    segments = [
        seg
        for seg in session.get("structure") or []
        if isinstance(seg, dict) and format_duration(seg.get("duration_min")) is not None
    ]
    steps = []
    for i, seg in enumerate(segments):
        duration = format_duration(seg.get("duration_min"))
        target = step_target(discipline, seg.get("zone"), seg.get("target_power_w"), profile)
        steps.append(_step_line(_cue(seg.get("segment")), duration, target, _intensity(segments, i)))

    if not steps:
        # No usable structure: one step for the whole session, with a target
        # only when the session names a single zone.
        duration = format_duration(session.get("duration_min"))
        if duration is None:
            raise ValueError("This session has no duration to send to the watch.")
        zones = list((session.get("zones_target") or {}).keys())
        target = step_target(discipline, zones[0], None, profile) if len(zones) == 1 else None
        steps.append(_step_line("", duration, target, "active"))

    heading = _heading(discipline, session.get("type"))
    benefit = _benefit(session)
    return (
        (f"{heading}\n\n" if heading else "")
        + "\n\n".join("\n".join(block) for block in _fold_repeats(steps))
        + "\n"
        + (f"\nBenefit - {benefit}\n" if benefit else "")
    )


# What each plan session type trains. "intervals" is refined by zone below.
TYPE_BENEFIT = {
    "endurance": "aerobic base and fat oxidation",
    "long": "endurance and fatigue resistance",
    "tempo": "sustained aerobic power",
    "threshold": "threshold power and lactate clearance",
    "intervals": "speed and aerobic power",
    "recovery": "recovery and freshness",
    "technique": "form and efficiency",
    "brick": "bike-to-run transition legs",
}


def _heading(discipline: str, session_type) -> str:
    """"Run intervals"; just "Run" when the session has no type."""
    kind = re.sub(r"[^a-z ]", "", str(session_type or "").lower()).strip()
    return f"{discipline.capitalize()} {kind}".strip()


def _benefit(session: dict) -> str | None:
    kind = str(session.get("type") or "").lower().strip()
    if str(session.get("discipline") or "").lower() == "strength":
        return "strength and injury resilience"
    if kind == "intervals":
        zones = {
            z
            for seg in session.get("structure") or []
            if isinstance(seg, dict)
            for z in _zones(seg.get("zone")) or []
        }
        if "Z5" in zones:
            return "VO2max increase"
        if "Z4" in zones:
            return "threshold power increase"
    return TYPE_BENEFIT.get(kind)


def _step_line(cue: str, duration: str, target: str | None, intensity: str) -> str:
    parts = (cue, duration, target, f"intensity={intensity}")
    return "- " + " ".join(part for part in parts if part)


# Segment-name words that say what a step is, matched at the start of a word.
_WARMUP_NAME = re.compile(r"\bwarm", re.IGNORECASE)
_COOLDOWN_NAME = re.compile(r"\bcool", re.IGNORECASE)
_ACTIVE_NAME = re.compile(
    r"\b(interval|rep|hard|work|tempo|threshold|main|effort|stride|surge|fast|race)", re.IGNORECASE
)
_REST_NAME = re.compile(r"\b(recover|rest|easy|jog|float|walk)", re.IGNORECASE)

# A step at this zone or above is hard work; at or below EASY_ZONE it can be a
# warm-up, a recovery or a cool-down around it.
HARD_ZONE = 3
EASY_ZONE = 2


def _top_zone(seg: dict) -> int | None:
    """The highest zone a segment names: "Z1-Z2" -> 2; None when it names none."""
    zones = _zones(seg.get("zone"))
    return int(zones[-1][1]) if zones else None


def _intensity(segments: list[dict], i: int) -> str:
    """
    The step type for segment i: "warmup", "cooldown", "rest" or "active".

    The segment's name decides first. A recovery name ("Recovery", "Easy",
    "Jog") means rest only after the first step: a session that opens easy is
    warming up, and a one-step "Easy run" is the whole run, not a rest. Then,
    only in a session with hard work (Z3 or above), an easy step (Z2 or below)
    is the warm-up when first, the cool-down when last, and a rest when it sits
    next to a hard step. Everything else is active -- so a steady Z2 run or a
    Z1 recovery run is never sent as Rest.
    """
    seg = segments[i]
    name = str(seg.get("segment") or "")
    first, last = i == 0, i == len(segments) - 1
    if _WARMUP_NAME.search(name):
        return "warmup"
    if _COOLDOWN_NAME.search(name):
        return "cooldown"
    if _ACTIVE_NAME.search(name):
        return "active"
    if _REST_NAME.search(name) and not first:
        return "rest"

    top = _top_zone(seg)
    hard_session = any((_top_zone(s) or 0) >= HARD_ZONE for s in segments)
    if hard_session and top is not None and top <= EASY_ZONE:
        if first:
            return "warmup"
        if last:
            return "cooldown"
        if any((_top_zone(n) or 0) >= HARD_ZONE for n in (segments[i - 1], segments[i + 1])):
            return "rest"
    return "active"


# The longest rep folded into a repeat, in steps (as MAX_PATTERN in derive.ts).
MAX_PATTERN = 3


def _fold_repeats(lines: list[str]) -> list[list[str]]:
    """
    Step lines as blocks: runs of single steps, and repeats opened by an "Nx"
    line. intervals.icu ends a repeat at a blank line, so the caller joins the
    blocks with one. Lines are compared as rendered: _cue has already dropped
    the rep numbers, and notes never reach a line.
    """
    blocks: list[list[str]] = []
    loose: list[str] = []
    i = floor = 0
    while i < len(lines):
        # Widest coverage wins; on a tie the shorter pattern (4x A, not 2x (A, A)).
        best = None
        for length in range(1, MAX_PATTERN + 1):
            found = _repeat_at(lines, i, length, floor)
            if found and (best is None or found[1] > best[1]):
                best = found
        if best is None:
            loose.append(lines[i])
            i += 1
            continue
        reps, end, length = best
        if loose:
            blocks.append(loose)
        blocks.append([f"{reps}x", *lines[i : i + length]])
        # A short last rep goes after the repeat as plain steps.
        loose = lines[i + reps * length : end]
        i = floor = end
    if loose:
        blocks.append(loose)
    return blocks


def _repeat_at(lines: list[str], start: int, length: int, floor: int):
    """
    (full reps, end, length) for the repeat of `length` steps at `start`, where
    `end` also covers a short last rep (one that stops before its final step);
    None for fewer than two full reps. `floor` is the first step not already in
    an earlier repeat.
    """
    if start + length > len(lines):
        return None
    # A run starts where it really starts: when the loose step before `start`
    # matches this pattern's last step, the run began there, and folding from
    # here would anchor it part-way into a rep.
    if start > floor and lines[start - 1] == lines[start + length - 1]:
        return None

    def same(at: int, n: int) -> bool:
        return at + n <= len(lines) and lines[at : at + n] == lines[start : start + n]

    reps, end = 1, start + length
    while same(end, length):
        reps += 1
        end += length
    if reps < 2:
        return None
    if length > 1 and same(end, length - 1):
        end += length - 1
    return reps, end, length


def format_duration(minutes) -> str | None:
    """Minutes as intervals.icu writes a step length: 1h30m, 12m, 2m30s."""
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        return None
    seconds = round(minutes * 60)
    if seconds <= 0:
        return None
    hours, rest = divmod(seconds, 3600)
    mins, secs = divmod(rest, 60)
    return "".join(f"{n}{unit}" for n, unit in ((hours, "h"), (mins, "m"), (secs, "s")) if n)


def _zones(zone) -> list[str] | None:
    """"Z4" -> ["Z4"]; "Z1-Z2" -> ["Z1", "Z2"]; anything else -> None."""
    m = _ZONE_RE.match(str(zone or ""))
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    lo, hi = min(lo, hi), max(lo, hi)
    return [f"Z{n}" for n in range(lo, hi + 1)]


def _band(bands: dict, zones: list[str]) -> tuple[float, float]:
    """The union of the zones' bands: the first one's low end to the last one's high end."""
    return min(bands[z][0] for z in zones), max(bands[z][1] for z in zones)


def _positive(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def step_target(discipline: str, zone, target_power_w, profile: dict) -> str | None:
    """One step's target in intervals.icu syntax, or None for a step with no target."""
    zones = _zones(zone)
    if discipline == "run":
        if not zones:
            return None
        lo, hi = _band(RUN_HR_BANDS, zones)
        return f"{lo}-{hi}% LTHR"
    if discipline == "bike":
        return _bike_target(zones, target_power_w, _positive(profile.get("ftp_w")))
    if discipline == "swim":
        css = _positive(profile.get("css_sec_100m"))
        if not zones or css is None:
            return None
        fast, slow = _band(SWIM_PACE_BANDS, zones)
        return f"{_mmss(css * fast)}/100m-{_mmss(css * slow)}/100m Pace"
    return None  # strength: time only


def _bike_target(zones: list[str] | None, target_power_w, ftp: float | None) -> str | None:
    # The watts Claude wrote win over the zone: they are what the modal shows.
    m = _WATTS_RE.match(str(target_power_w or ""))
    if m:
        lo = float(m.group(1))
        hi = float(m.group(2) or lo)
        lo, hi = min(lo, hi), max(lo, hi)
        if ftp is not None and lo < ftp * BIKE_FLOOR_PCT / 100:
            lo = min(ftp * BIKE_FLOOR_PCT / 100, hi)
    elif zones and ftp is not None:
        lo_pct, hi_pct = _band(BIKE_POWER_BANDS, zones)
        lo, hi = ftp * lo_pct / 100, ftp * hi_pct / 100
    else:
        return None
    lo, hi = round(lo), round(hi)
    if hi <= 0:
        return None
    return f"{hi}w" if lo == hi else f"{lo}-{hi}w"


def _mmss(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _cue(name) -> str:
    """
    A segment name as a step cue, with every word intervals.icu could misread
    as a duration or target dropped: "Interval 1" -> "Interval", "Z2 main set"
    -> "main set". The parser takes the text before the first duration as the
    cue, so a stray "3" or "Z2" there could change the step.
    """
    words = []
    for token in str(name or "").split():
        if any(ch.isdigit() for ch in token):
            continue
        for word in re.sub(r"[\W_]", " ", token).split():
            if len(word) > 1 and word.lower() not in _CUE_STOPWORDS:
                words.append(word)
    return " ".join(words)
