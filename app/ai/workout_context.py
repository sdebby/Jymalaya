"""
A completed workout's full recorded data, for the coach chat.

When the athlete sends a workout to the chat (Ask coach in the workout modal,
or /last), the browser names it by activity id and this module writes the
block the coach's system context carries for it: everything the workout modal
shows -- the summary, load impact, Training Effect, RPE and feel, time in HR
and power zones, the derived metrics, the planned workout, the laps, the 30 s
streams its graphs draw, and the cached analysis.

Kept small on purpose: tables are CSV (column names once, no JSON keys per
row), numbers are rounded to what the graphs show, and paces are already
min:sec so the model never converts seconds (rule P8). The block rides in the
system context once per attached workout, so it is never repeated through the
chat history.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from app.ai.prompt_builder import _workout_date, load_stored_workout
from app.config import get_athlete_profile
from app.influx.client import InfluxRepo
from app.metrics.analysis import build_derived_metrics, power_zone_split
from app.workout_detail import bucket_stream, stored_laps, stored_planned, stored_samples

log = logging.getLogger("jymalaya.coach")

# The graphs draw no pace below this speed (a stop, not a pace).
MIN_SPEED_MPS = 0.5
# At most this many attached workouts reach one turn (the route caps the list too).
MAX_ATTACHED = 3

# Garmin's feel answer, 0-100 in steps of 25 (WorkoutRating.tsx's bands).
FEEL_LABELS = ("Very Weak", "Weak", "Normal", "Strong", "Very Strong")


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mmss(seconds: float | None) -> str:
    """Seconds as m:ss (a pace or a duration under an hour)."""
    if seconds is None:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _pace_unit(discipline: str) -> tuple[int, str] | None:
    """Metres per pace unit and its label, for the disciplines drawn as pace."""
    if discipline == "run":
        return 1000, "/km"
    if discipline == "swim":
        return 100, "/100m"
    return None


def _pace_from_speed(speed: float | None, per: int) -> str:
    if speed is None or speed < MIN_SPEED_MPS:
        return ""
    return _mmss(per / speed)


def _r(v, digits: int = 0) -> str:
    """A rounded number as text; '' for a missing value."""
    x = _num(v)
    if x is None:
        return ""
    if digits == 0:
        return str(int(round(x)))
    return f"{x:.{digits}f}"


def _csv(header: list[str], rows: list[list[str]]) -> list[str]:
    """CSV lines with every column that no row fills left out."""
    keep = [i for i in range(len(header)) if any(r[i] != "" for r in rows)]
    if not keep:
        return []
    out = [",".join(header[i] for i in keep)]
    out += [",".join(r[i] for i in keep) for r in rows]
    return out


def _summary(w: dict) -> str:
    d = w.get("discipline") or "other"
    parts: list[str] = []
    if _num(w.get("duration_sec")):
        parts.append(f"{_r(w['duration_sec'] / 60)} min")
    if (moving := _num(w.get("moving_duration_sec"))):
        parts.append(f"moving {_r(moving / 60)} min")
    if (dist := _num(w.get("distance_m"))):
        parts.append(f"{_r(dist)} m" if d == "swim" else f"{dist / 1000:.2f} km")
    if w.get("tss") is not None:
        method = f" ({w['tss_method']})" if w.get("tss_method") else ""
        parts.append(f"TSS {_r(w['tss'])}{method}")
    if w.get("intensity_factor") is not None:
        parts.append(f"IF {_r(w['intensity_factor'], 2)}")
    if w.get("avg_hr") is not None:
        mx = f", max {_r(w['max_hr'])}" if w.get("max_hr") is not None else ""
        parts.append(f"HR avg {_r(w['avg_hr'])}{mx} bpm")
    if d == "bike" and w.get("avg_pace_sec_per_km"):
        parts.append(f"speed avg {3600 / float(w['avg_pace_sec_per_km']):.1f} km/h")
    elif w.get("avg_pace_sec_per_km"):
        parts.append(f"pace avg {_mmss(_num(w['avg_pace_sec_per_km']))}/km")
    if w.get("ngp_sec_per_km"):
        parts.append(f"NGP {_mmss(_num(w['ngp_sec_per_km']))}/km")
    if w.get("avg_pace_sec_per_100m"):
        parts.append(f"pace avg {_mmss(_num(w['avg_pace_sec_per_100m']))}/100m")
    power = [
        f"{label} {_r(w[key])}"
        for key, label in (("avg_power", "avg"), ("normalized_power", "NP"), ("max_power", "max"))
        if w.get(key) is not None
    ]
    if power:
        parts.append(f"power {', '.join(power)} W")
    if w.get("avg_cadence") is not None:
        unit = "rpm" if d == "bike" else "str/min" if d == "swim" else "spm"
        parts.append(f"cadence avg {_r(w['avg_cadence'])} {unit}")
    if w.get("elevation_gain_m") is not None or w.get("elevation_loss_m") is not None:
        parts.append(f"elevation +{_r(w.get('elevation_gain_m')) or 0}/-{_r(w.get('elevation_loss_m')) or 0} m")
    if w.get("calories") is not None:
        parts.append(f"{_r(w['calories'])} kcal")
    return "summary: " + "; ".join(parts)


def _ratings(w: dict) -> list[str]:
    lines = []
    tss = _num(w.get("tss"))
    if tss and tss > 0:
        ctl, atl = tss / 42, tss / 7
        lines.append(
            f"load impact against a rest day: fitness (CTL) +{ctl:.1f}, "
            f"fatigue (ATL) +{atl:.1f}, form (TSB) {ctl - atl:+.1f}"
        )
    te = []
    if w.get("aerobic_te") is not None:
        te.append(f"aerobic {_r(w['aerobic_te'], 1)}")
    if w.get("anaerobic_te") is not None:
        te.append(f"anaerobic {_r(w['anaerobic_te'], 1)}")
    if w.get("te_label"):
        te.append(f"primary benefit {str(w['te_label']).replace('_', ' ').lower()}")
    if te:
        lines.append("Garmin Training Effect (0-5): " + ", ".join(te))
    rating = []
    if w.get("rpe") is not None:
        rating.append(f"RPE {_r(w['rpe'])}/10")
    if (feel := _num(w.get("feel"))) is not None:
        step = min(4, max(0, round(feel / 25)))
        rating.append(f"feel {FEEL_LABELS[step]}")
    if rating:
        lines.append("athlete's own rating: " + ", ".join(rating))
    return lines


def _zones(w: dict, power_zones: dict | None) -> list[str]:
    lines = []
    raw = w.get("zones_time_sec")
    try:
        hr = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        hr = None
    if isinstance(hr, dict) and hr:
        lines.append(
            "time in HR zones (% LTHR), min: "
            + ", ".join(f"{z} {_r((_num(s) or 0) / 60, 1)}" for z, s in sorted(hr.items()))
        )
    if power_zones:
        lines.append(
            "time in power zones (% FTP), min: "
            + ", ".join(f"{z} {_r((_num(s) or 0) / 60, 1)}" for z, s in power_zones.items())
        )
    return lines


def _derived(derived: dict, discipline: str) -> list[str]:
    if derived.get("status") != "ok":
        return []
    lines = []
    dec = derived.get("aerobic_decoupling") or {}
    if dec.get("status") == "ok":
        lines.append(
            f"aerobic decoupling ({dec['metric']}, efficiency first half vs second): "
            f"{dec['decoupling_pct']:.1f}%"
        )
    elif dec.get("reason"):
        lines.append(f"aerobic decoupling n/a: {dec['reason']}")
    halves = derived.get("half_splits") or {}
    if halves.get("status") == "ok":
        bits = []
        if "hr" in halves:
            h = halves["hr"]
            bits.append(f"HR {_r(h['first_half'])} -> {_r(h['second_half'])}")
        if "speed_mps" in halves:
            s = halves["speed_mps"]
            unit = _pace_unit(discipline)
            if unit:
                per, label = unit
                bits.append(
                    f"pace {_pace_from_speed(s['first_half'], per)} -> "
                    f"{_pace_from_speed(s['second_half'], per)}{label}"
                )
            else:
                bits.append(f"speed {s['first_half'] * 3.6:.1f} -> {s['second_half'] * 3.6:.1f} km/h")
        if "power_w" in halves:
            p = halves["power_w"]
            bits.append(f"power {_r(p['first_half'])} -> {_r(p['second_half'])} W")
        if "cadence" in halves:
            c = halves["cadence"]
            bits.append(f"cadence {_r(c['first_half'])} -> {_r(c['second_half'])}")
        if bits:
            lines.append("first half -> second half: " + "; ".join(bits))
    cad = derived.get("cadence_stability") or {}
    if cad.get("status") == "ok":
        cv = cad.get("coefficient_of_variation_pct")
        lines.append(f"cadence mean {_r(cad['mean'])}, sd {_r(cad['std_dev'], 1)}" + (f", CV {cv}%" if cv is not None else ""))
    if derived.get("normalized_power_from_stream_w") is not None:
        lines.append(f"NP from the stream: {_r(derived['normalized_power_from_stream_w'])} W")
    return lines


def _laps(laps: list[dict], discipline: str) -> list[str]:
    if len(laps) < 2:
        return []
    unit = _pace_unit(discipline)
    speed_col = f"pace{unit[1]}" if unit else "kmh"
    header = ["lap", "min", "km", "hr", "max_hr", speed_col, "w", "np", "max_w", "cad", "elev_gain", "type"]
    rows = []
    for lap in laps:
        speed = _num(lap.get("avg_speed_mps"))
        speed_text = _pace_from_speed(speed, unit[0]) if unit else (f"{speed * 3.6:.1f}" if speed else "")
        dist = _num(lap.get("distance_m"))
        rows.append([
            str(lap.get("lap", "")),
            _r((_num(lap.get("duration_sec")) or 0) / 60, 1) if lap.get("duration_sec") is not None else "",
            f"{dist / 1000:.2f}" if dist is not None else "",
            _r(lap.get("avg_hr")),
            _r(lap.get("max_hr")),
            speed_text,
            _r(lap.get("avg_power_w")),
            _r(lap.get("norm_power_w")),
            _r(lap.get("max_power_w")),
            _r(lap.get("avg_cadence")),
            _r(lap.get("elevation_gain_m")),
            str(lap.get("intensity") or ""),
        ])
    lines = _csv(header, rows)
    return ["laps (CSV):", *lines] if lines else []


def _streams(stream: dict | None, discipline: str) -> list[str]:
    samples = (stream or {}).get("samples") or []
    if len(samples) < 2:
        return ["recorded streams: none stored"]
    unit = _pace_unit(discipline)
    speed_col = f"pace{unit[1]}" if unit else "kmh"
    header = ["min", speed_col, "hr", "w", "cad", "elev", "km"]
    rows = []
    for s in samples:
        speed = _num(s.get("speed_mps"))
        if unit:
            speed_text = _pace_from_speed(speed, unit[0])
        else:
            speed_text = f"{speed * 3.6:.1f}" if speed is not None else ""
        dist = _num(s.get("distance_m"))
        rows.append([
            f"{(_num(s.get('t')) or 0) / 60:.1f}",
            speed_text,
            _r(s.get("hr")),
            _r(s.get("power_w")),
            _r(s.get("cadence")),
            # The modal draws no elevation for a swim.
            "" if discipline == "swim" else _r(s.get("elevation_m")),
            f"{dist / 1000:.2f}" if dist is not None else "",
        ])
    lines = _csv(header, rows)
    bucket = stream.get("bucket_sec") or 30
    speed_text = "pace min:sec, blank when stopped" if unit else "kmh speed in km/h"
    legend = (
        f"recorded streams, one row per {bucket} s, averaged (CSV). min = moving "
        f"time in minutes, pauses removed; {speed_text}; hr bpm; w watts; "
        "cad cadence; elev metres; km distance so far:"
    )
    return [legend, *lines]


def _analysis(analysis: dict | None) -> list[str]:
    if not analysis:
        return []
    lines = ["your earlier analysis of this workout:"]
    adherence = analysis.get("plan_adherence") or {}
    if adherence.get("verdict") or adherence.get("detail"):
        lines.append(f"- execution vs plan: {adherence.get('verdict') or ''} {adherence.get('detail') or ''}".rstrip())
    form = analysis.get("form_context")
    if isinstance(form, str):
        form = [t.strip() for t in re.split(r"(?<=[.!?])\s+(?=[A-Z])", form) if t.strip()]
    for label, items in (
        ("went well", analysis.get("what_went_well")),
        ("to improve", analysis.get("what_to_improve")),
        ("flag", analysis.get("flags")),
        ("against current form", form),
    ):
        for item in items or []:
            if item:
                lines.append(f"- {label}: {item}")
    return lines if len(lines) > 1 else []


def format_workout_block(
    workout: dict,
    *,
    stream: dict | None,
    laps: list[dict],
    planned: dict | None,
    derived: dict,
    power_zones: dict | None,
    analysis: dict | None,
) -> str:
    """One attached workout as compact text. Pure."""
    discipline = workout.get("discipline") or "other"
    day = _workout_date(workout) or ""
    try:
        weekday = date.fromisoformat(day).strftime("%A") + " "
    except ValueError:
        weekday = ""
    title = f', "{workout["title"]}"' if workout.get("title") else ""
    brick = ", a brick leg" if workout.get("is_brick_split") else ""
    lines = [
        f"### Workout {workout.get('activity_id')}: {weekday}{day}, {discipline}{title}{brick}",
        _summary(workout),
        *_ratings(workout),
        *_zones(workout, power_zones),
        *_derived(derived, discipline),
    ]
    if planned:
        lines.append("planned workout it was run against: " + json.dumps(planned, separators=(",", ":")))
    lines += _laps(laps, discipline)
    lines += _streams(stream, discipline)
    lines += _analysis(analysis)
    return "\n".join(lines)


def _block_for(repo: InfluxRepo, activity_id: str, profile: dict) -> str | None:
    from app.analysis_service import cached_analysis  # imports the claude layer

    workout = load_stored_workout(repo, activity_id)
    if workout is None:
        log.info("Attached workout %s is not stored; left out", activity_id)
        return None
    rows = stored_samples(repo, activity_id)
    stream = bucket_stream(rows, workout.get("duration_sec")) if rows else None
    power_zones = None
    if stream and workout.get("discipline") == "bike":
        split = power_zone_split(stream["samples"], profile.get("ftp_w"), stream["bucket_sec"])
        if split.get("status") == "ok":
            power_zones = split["seconds"]
    return format_workout_block(
        workout,
        stream=stream,
        laps=stored_laps(repo, activity_id) if rows else [],
        planned=stored_planned(repo, activity_id),
        derived=build_derived_metrics(workout, stream, profile),
        power_zones=power_zones,
        analysis=cached_analysis(activity_id),
    )


def attached_workouts_context(activity_ids: list[str]) -> str:
    """
    The system-context section for the workouts attached in this conversation,
    or "" when none could be read. Never raises: a workout that fails to load
    is logged and left out, and the turn goes ahead without it.
    """
    ids = list(dict.fromkeys(activity_ids))[:MAX_ATTACHED]
    if not ids:
        return ""
    blocks: list[str] = []
    try:
        profile = get_athlete_profile()
        repo = InfluxRepo()
    except Exception:
        log.warning("Could not open storage for the attached workouts", exc_info=True)
        return ""
    try:
        for activity_id in ids:
            try:
                block = _block_for(repo, activity_id, profile)
            except Exception:
                log.warning("Could not build the attached workout %s", activity_id, exc_info=True)
                continue
            if block:
                blocks.append(block)
    finally:
        repo.close()
    if not blocks:
        return ""
    return (
        "## Attached workouts\n"
        "The athlete attached these completed workouts in this conversation; the "
        "messages name each by its activity id. This is their full recorded data, "
        "the same the workout's detail view shows. Read the streams and laps to "
        "answer, and quote paces as min:sec, never in seconds.\n\n"
        + "\n\n".join(blocks)
    )
