"""
What the season plan looks like as of a day: each week's status and the two
template-written summary lines. Computed on every read, never by Claude, and
never stored as a commitment -- the plan file only changes on sync and on the
athlete's own edits.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.season import config as cfg
from app.season import split

BAND_LABEL = {"fresh": "Fresh", "ok": "OK", "tired": "Tired", "high": "High"}
KIND_PHRASE = {
    "recovery": "a recovery week",
    "return": "a return week",
    "taper": "a taper week",
    "race": "race week",
}


def _sub_name(w: dict) -> str:
    return (w.get("sub_event") or {}).get("name") or "the sub event"


def _kind_phrase(w: dict) -> str:
    if w["kind"] == "sub_race":
        return f"race week for {_sub_name(w)}"
    if w["kind"] == "sub_taper":
        return f"a mini-taper for {_sub_name(w)}"
    return KIND_PHRASE.get(w["kind"]) or f"a {w['phase']} week"


def band_for(tsb: float | None, previous_tsb: float | None = None) -> str | None:
    """
    The fatigue band of a TSB, on the Friel zones the Fatigue Zones chart
    draws. Two readings in a row below BAND_HIGH_REPEAT also count as high.
    """
    if tsb is None:
        return None
    if tsb < cfg.BAND_TIRED_MIN or (
        previous_tsb is not None
        and tsb < cfg.BAND_HIGH_REPEAT
        and previous_tsb < cfg.BAND_HIGH_REPEAT
    ):
        return "high"
    if tsb < cfg.BAND_OK_MIN:
        return "tired"
    if tsb < cfg.BAND_FRESH_MIN:
        return "ok"
    return "fresh"


def _day_mon(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def week_statuses(weeks: list[dict], today: date) -> None:
    """past / current / next / projected for each week, in place."""
    seen_future = False
    for w in weeks:
        ws = date.fromisoformat(w["week_start"])
        if ws + timedelta(days=6) < today:
            w["status"] = "past"
        elif ws <= today:
            w["status"] = "current"
        elif not seen_future:
            # The week after the current one, or the plan's first week when it
            # has not started yet.
            w["status"] = "next"
            seen_future = True
        else:
            w["status"] = "projected"


def _fit(line: str, shorter: list[str]) -> str:
    """The first candidate under the length limit, else a hard cut."""
    for candidate in [line, *shorter]:
        if len(candidate) < cfg.SUMMARY_MAX_CHARS:
            return candidate
    return candidate[: cfg.SUMMARY_MAX_CHARS - 4].rstrip() + "..."


def _phase_counts(weeks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for w in weeks:
        counts[w["phase"]] = counts.get(w["phase"], 0) + 1
    return counts


def event_line1(plan: dict) -> str:
    """What the plan is and its shape, with the projected peak CTL."""
    weeks = plan["weeks"]
    event = plan.get("event") or {}
    name = event.get("name") or "the race"
    race = date.fromisoformat(event["race_date"])
    counts = _phase_counts(weeks)
    parts = [f"{counts[p]} {p}" for p in ("base", "build", "peak", "taper") if counts.get(p)]
    build = [w for w in weeks if w["phase"] in ("base", "build", "peak")]
    n = len(weeks)
    head = f"{n} week{'s' if n != 1 else ''} to {name}"
    shape = ", ".join(parts + ["then race week"]) if parts else "race week only"
    if build:
        last = build[-1]
        tail = f"Projected CTL {round(last['ctl_end'] or 0)} by the last {last['phase']} week"
        start_ctl = (plan.get("baseline") or {}).get("ctl")
        origin = f" (from {round(start_ctl)})" if start_ctl is not None else ""
    else:
        tail, origin = "Too close to the race for a build: taper and race only", ""
    targets = plan.get("targets") or {}
    compressed = (
        f" Compressed: peak lowered to {round(targets['peak_weekly_tss'])} TSS/week."
        if targets.get("compressed")
        else ""
    )
    subs = len(plan.get("sub_events") or [])
    sub_text = f" {subs} sub event{'s' if subs != 1 else ''} on the way." if subs else ""
    full = f"{head} ({_day_mon(race)}): {shape}. {tail}{origin}.{compressed}{sub_text}"
    return _fit(
        full,
        [
            f"{head} ({_day_mon(race)}): {shape}. {tail}{origin}.{compressed}",
            f"{head}: {shape}. {tail}.{compressed}",
            f"{head}: {shape}.{compressed}",
        ],
    )


def event_line2(plan: dict, today: date) -> str:
    """Where the athlete is now: this week, what is next, and form."""
    weeks = plan["weeks"]
    form = plan.get("last_form") or plan.get("baseline") or {}
    tsb = form.get("tsb")
    band = band_for(tsb)
    form_text = f"form is {BAND_LABEL[band]} (TSB {round(tsb)})" if band else ""

    current = next((w for w in weeks if w["status"] == "current"), None)
    upcoming = next((w for w in weeks if w["status"] == "next"), None)
    if current is None:
        if upcoming is None:
            return _fit("The race is done. Recovery first, then start a new plan.", [])
        ws = date.fromisoformat(upcoming["week_start"])
        line = (
            f"Starts {_day_mon(ws)} with a {upcoming['phase']} week of "
            f"{round(upcoming['target_tss'])} TSS"
        )
        return _fit(f"{line}; {form_text}." if form_text else f"{line}.", [f"{line}."])

    what = _kind_phrase(current)
    done = round(current.get("actual_tss") or 0)
    line = (
        f"This week is {what} ({current['index']} of {len(weeks)}): "
        f"{round(current['target_tss'])} TSS target, {done} done."
    )
    if upcoming is None:
        nxt = ""
    elif upcoming["kind"] == "race":
        nxt = "Race week is next"
    elif upcoming["kind"] == "sub_race":
        nxt = f"{_sub_name(upcoming)} race week is next"
    elif upcoming["kind"] == "sub_taper":
        nxt = f"Mini-taper for {_sub_name(upcoming)} next week"
    elif upcoming["phase"] != current["phase"]:
        nxt = f"{upcoming['phase'].capitalize()} phase starts next week"
    elif upcoming["kind"] == "recovery":
        nxt = "Recovery week next"
    else:
        nxt = f"Next week {round(upcoming['target_tss'])} TSS"
    rest = "; ".join(p for p in (nxt, form_text) if p)
    full = f"{line} {rest[0].upper() + rest[1:]}." if rest else line
    return _fit(full, [f"{line} {nxt}." if nxt else line, line])


def annotate(plan: dict, today: date) -> dict:
    """Statuses and summary lines for `today`, in place; returns the plan."""
    plan["as_of"] = today.isoformat()
    week_statuses(plan.get("weeks") or [], today)
    split.add_view(split.apply_splits(plan))
    if plan.get("mode") == "event" and plan.get("event"):
        plan["summary"] = {"line1": event_line1(plan), "line2": event_line2(plan, today)}
    return plan
