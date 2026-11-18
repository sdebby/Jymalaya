"""
The swim / bike / run split of a season plan. Pure: no I/O.

Every week of a triathlon plan (an event of a TRI_TYPES distance, or a fitness
plan with the triathlon focus) carries `split`, integer percents of its TSS
target. So does any other plan whose coach instructions change its sports
(app/season/instructions.py) -- a run plan with one ride a week -- starting
from a one-sport row ({run: 100}). The table a week follows is layered: the
code row, then the instructions, then the athlete's own split_by_phase. The
race week keeps the race sports only: instructions never reach it.

`split_source` is "table" while a week follows that table, "user" once the
athlete (or the coach, on their word) set it by hand, and "kept" when a later
change to the instructions left it as it was (the week had already started).
A user split is separate from the TSS pin -- the week's target still adapts --
and survives every rebuild, reflow and settings change.

apply_splits() is idempotent and runs on every build and read (annotate) and
before every save, so the builders, the adaptation rules and the rebuild need
no split code of their own: a week whose phase changed simply takes its new
phase's row the next time it passes through here.
"""

from __future__ import annotations

from app.models import race_disciplines
from app.season import config as cfg
from app.season.instructions import apply_to_row, changes_sports

ACTUAL_DISCIPLINES = ("swim", "bike", "run", "strength")
# Read-only fields view() adds; never stored.
VIEW_FIELDS = ("discipline_tss", "discipline_hours")


class SplitError(ValueError):
    """A split that is not three whole percents adding up to 100."""


def is_triathlon(plan: dict) -> bool:
    if plan.get("mode") == "fitness":
        return ((plan.get("settings") or {}).get("focus") or "triathlon") == "triathlon"
    return ((plan.get("event") or {}).get("type")) in cfg.TRI_TYPES


def _one_sport(sport: str) -> dict[str, int]:
    return {d: (100 if d == sport else 0) for d in cfg.SPLIT_DISCIPLINES}


def default_table(plan: dict) -> dict[str, dict[str, int]]:
    """
    The code table's rows for this plan: one per phase, or `fitness`. A
    triathlon has the per-phase table; a one-sport event or fitness focus a
    one-sport row (it shows only once instructions add a sport); a custom
    event has none.
    """
    if plan.get("mode") == "fitness":
        focus = (plan.get("settings") or {}).get("focus") or "triathlon"
        if focus == "triathlon":
            return {"fitness": dict(cfg.FITNESS_TRI_SPLIT)}
        return {"fitness": _one_sport(focus)} if focus in cfg.SPLIT_DISCIPLINES else {}
    event_type = (plan.get("event") or {}).get("type")
    rows = cfg.DISCIPLINE_SPLIT.get(event_type)
    if rows:
        return {phase: dict(row) for phase, row in rows.items()}
    sports = race_disciplines(event_type) or []
    if len(sports) == 1 and sports[0] in cfg.SPLIT_DISCIPLINES:
        return {phase: _one_sport(sports[0]) for phase in cfg.SPLIT_PHASES}
    return {}


def instruction_read(plan: dict) -> dict | None:
    """The coach instructions' read the plan applies, if any."""
    rec = plan.get("instructions") or {}
    return rec.get("read") if rec.get("applied") else None


def has_split(plan: dict) -> bool:
    """A triathlon, or a plan whose instructions change its sports."""
    return is_triathlon(plan) or (bool(default_table(plan)) and changes_sports(instruction_read(plan)))


def split_table(plan: dict, with_instructions: bool = True) -> dict[str, dict[str, int]]:
    """The code table, then the instructions' sports, then the plan's own
    split_by_phase (the athlete's table wins)."""
    table = default_table(plan)
    read = instruction_read(plan) if with_instructions else None
    if changes_sports(read):
        table = {phase: apply_to_row(row, read) for phase, row in table.items()}
    own = (plan.get("settings") or {}).get("split_by_phase") or {}
    for phase, row in own.items():
        if phase in table and row:
            table[phase] = dict(row)
    return table


def _row_for(table: dict, phase: str) -> dict[str, int] | None:
    if "fitness" in table:
        return table["fitness"]
    return table.get("taper" if phase == "race" else phase)


def validate_split(split: dict | None) -> dict[str, int]:
    """Three whole percents, swim/bike/run, each 0-100, adding up to 100."""
    if not isinstance(split, dict) or set(split) != set(cfg.SPLIT_DISCIPLINES):
        raise SplitError("A split needs swim, bike and run, nothing else.")
    out: dict[str, int] = {}
    for d in cfg.SPLIT_DISCIPLINES:
        v = split[d]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not 0 <= v <= 100:
            raise SplitError(f"The {d} share must be a whole percent from 0 to 100.")
        out[d] = int(v)
    if sum(out.values()) != 100:
        raise SplitError(f"The split adds up to {sum(out.values())}%, not 100%.")
    return out


def validate_table(plan: dict, table: dict | None) -> dict[str, dict[str, int]]:
    """A split_by_phase from the create form or the settings: known phases only."""
    known = default_table(plan)
    if not isinstance(table, dict):
        raise SplitError("The split table must give a split per phase.")
    out = {}
    for phase, row in table.items():
        if phase not in known:
            raise SplitError(f"Unknown phase in the split table: {phase}.")
        out[phase] = validate_split(row)
    return out


FIXED_SOURCES = ("user", "kept")


def apply_splits(plan: dict) -> dict:
    """
    Give every week its split, in place; returns the plan. Weeks split by hand
    ("user") or left as they were by an instructions change ("kept") keep
    theirs; a race week follows the table without the instructions.
    """
    weeks = plan.get("weeks") or []
    if not has_split(plan):
        for w in weeks:
            if w.get("split_source") == "kept":
                continue
            for key in ("split", "split_source"):
                w.pop(key, None)
        (plan.get("settings") or {}).pop("split_by_phase", None)
        return plan
    table = split_table(plan)
    race_table = split_table(plan, with_instructions=False)
    for w in weeks:
        if w.get("split_source") in FIXED_SOURCES:
            continue
        phase = w.get("phase") or ""
        row = _row_for(race_table if phase == "race" else table, phase)
        if row is None:
            continue
        w["split"] = dict(row)
        w["split_source"] = "table"
    return plan


def keep_splits(plan: dict, upto: int) -> None:
    """Freeze the split of weeks[:upto] as it stands ("kept"; None when the
    week had none), so a change to the instructions applies after them only."""
    for w in (plan.get("weeks") or [])[:upto]:
        if w.get("split_source") != "user":
            w["split_source"] = "kept"
            w.setdefault("split", None)


def discipline_tss(target: float, split: dict[str, int]) -> dict[str, int]:
    """
    The target in TSS per discipline, whole numbers that add up to the rounded
    target exactly (largest remainder).
    """
    total = round(target or 0)
    raw = {d: total * split.get(d, 0) / 100 for d in cfg.SPLIT_DISCIPLINES}
    out = {d: int(v) for d, v in raw.items()}
    left = total - sum(out.values())
    for d in sorted(raw, key=lambda d: raw[d] - out[d], reverse=True)[:max(left, 0)]:
        out[d] += 1
    return out


def week_actual(got: dict | None) -> dict[str, dict] | None:
    """A week's actual TSS and hours per discipline, from weekly_totals."""
    by = (got or {}).get("by_discipline")
    if by is None:
        return None
    return {
        d: {"tss": round(v["tss"]), "hours": round(v["hours"], 1)}
        for d, v in by.items()
        if d in ACTUAL_DISCIPLINES and (v["tss"] > 0 or v["hours"] > 0)
    }


def set_actual(w: dict, got: dict | None) -> None:
    """A started week's actual totals, overall and per discipline, in place."""
    got = got or {"tss": 0.0, "hours": 0.0, "by_discipline": {}}
    w["actual_tss"] = round(got["tss"])
    w["actual_hours"] = round(got["hours"], 1)
    by = week_actual(got)
    if by is not None:
        w["actual_by_discipline"] = by


def add_view(plan: dict) -> dict:
    """discipline_tss / discipline_hours per week, for reading; in place."""
    baseline = plan.get("baseline") or {}
    tph = float(baseline.get("tss_per_hour") or cfg.DEFAULT_TSS_PER_HOUR)
    by_tph = baseline.get("tss_per_hour_by_discipline") or {}
    for w in plan.get("weeks") or []:
        if not w.get("split"):
            for key in VIEW_FIELDS:
                w.pop(key, None)
            continue
        parts = discipline_tss(w["target_tss"], w["split"])
        w["discipline_tss"] = parts
        w["discipline_hours"] = {d: round(v / float(by_tph.get(d) or tph), 1) for d, v in parts.items()}
    return plan


def label(split: dict[str, int]) -> str:
    """'swim 20 / bike 43 / run 37'"""
    return " / ".join(f"{d} {split[d]}" for d in cfg.SPLIT_DISCIPLINES)
