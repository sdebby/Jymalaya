"""
Derived per-workout metrics for the post-workout analysis (PRD section 7.9).

These are computed HERE, in code, and handed to Claude already reduced. Asking a
language model to eyeball aerobic decoupling off a few hundred sample rows gets
plausible-looking numbers that are wrong; arithmetic belongs in Python and
interpretation belongs in the prompt.

Everything takes the bucketed stream from app/workout_detail.bucket_stream plus
the athlete profile, and returns plain JSON-able dicts. Nothing raises: a metric
that cannot be computed from what the session recorded is reported as
"not_applicable" with a reason, never as a misleading zero.
"""

from __future__ import annotations

import math

# %FTP power zones, the same bands prompts/training_zones.txt gives every Claude
# call (tests/test_training_zones_prompt.py keeps the two in step). Upper bound
# is exclusive except for Z5, which is open.
POWER_ZONE_BANDS = (
    ("Z1", 0.00, 0.56),
    ("Z2", 0.56, 0.76),
    ("Z3", 0.76, 0.91),
    ("Z4", 0.91, 1.06),
    ("Z5", 1.06, float("inf")),
)

# Decoupling over a session shorter than this says more about the warmup than
# about aerobic durability, so it is not reported.
MIN_DECOUPLING_SEC = 45 * 60

# Normalized power is defined on a 30 s rolling average raised to the fourth
# power. The stream is already bucketed, so the rolling window is measured in
# buckets rather than seconds.
NP_WINDOW_SEC = 30


def _values(samples: list[dict], key: str) -> list[float]:
    return [s[key] for s in samples if isinstance(s.get(key), (int, float))]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _skip(reason: str) -> dict:
    return {"status": "not_applicable", "reason": reason}


def _output_key(samples: list[dict]) -> str | None:
    """
    The metric that stands for 'work done' in this session: power whenever a
    meter recorded it, speed otherwise (pace is speed inverted, so the ratio
    against HR behaves the same way).

    Power wins regardless of discipline -- Pw:HR is the better signal wherever it
    exists, including a run recorded with a running power meter.
    """
    if _values(samples, "power_w"):
        return "power_w"
    if _values(samples, "speed_mps"):
        return "speed_mps"
    return None


def aerobic_decoupling(samples: list[dict], duration_sec: float | None) -> dict:
    """
    Pw:HR (with a power meter) or Pa:HR (otherwise) drift, first half vs
    second half.

    Efficiency is output/HR. If the second half needs a higher heart rate for the
    same output, the ratio falls and decoupling is positive; under ~5% is the
    usual marker of an aerobically durable session. Reported only for steady work
    of at least MIN_DECOUPLING_SEC -- over intervals the number is dominated by
    which repetitions landed in which half.
    """
    if (duration_sec or 0) < MIN_DECOUPLING_SEC:
        return _skip(f"session shorter than {MIN_DECOUPLING_SEC // 60} min")

    key = _output_key(samples)
    if not key:
        return _skip("no power or speed recorded")

    paired = [s for s in samples if isinstance(s.get(key), (int, float)) and s.get("hr")]
    if len(paired) < 4:
        return _skip("not enough paired output and heart-rate samples")

    half = len(paired) // 2
    ratios = []
    for chunk in (paired[:half], paired[half:]):
        output, hr = _mean([s[key] for s in chunk]), _mean([s["hr"] for s in chunk])
        if not output or not hr:
            return _skip("a half of the session has no usable average")
        ratios.append(output / hr)

    first, second = ratios
    return {
        "status": "ok",
        "metric": "Pw:HR" if key == "power_w" else "Pa:HR",
        "first_half_ratio": round(first, 4),
        "second_half_ratio": round(second, 4),
        "decoupling_pct": round((first - second) / first * 100, 2),
    }


def half_splits(samples: list[dict], discipline: str) -> dict:
    """
    First-half vs second-half averages: did the athlete negative-split, or fade?
    """
    usable = [s for s in samples if len(s) > 1]
    if len(usable) < 4:
        return _skip("not enough samples to split")

    half = len(usable) // 2
    out: dict = {"status": "ok"}
    for name, key in (("hr", "hr"), ("power_w", "power_w"), ("speed_mps", "speed_mps"), ("cadence", "cadence")):
        first, second = _mean(_values(usable[:half], key)), _mean(_values(usable[half:], key))
        if first is None or second is None:
            continue
        out[name] = {
            "first_half": round(first, 2),
            "second_half": round(second, 2),
            "change_pct": round((second - first) / first * 100, 2) if first else None,
        }
    if discipline != "swim" and "speed_mps" in out:
        # Pace is what a runner reads; speed is what Garmin records.
        for side in ("first_half", "second_half"):
            speed = out["speed_mps"][side]
            out.setdefault("pace_sec_per_km", {})[side] = round(1000.0 / speed, 1) if speed else None
    return out


def power_zone_split(samples: list[dict], ftp_w: float | None, bucket_sec: int) -> dict:
    """
    Seconds in each %FTP power zone, from the stream. The HR-zone split already
    lives on the stored workout (zones_time_sec); this is its power counterpart
    and exists only for rides with a meter.
    """
    if not ftp_w or ftp_w <= 0:
        return _skip("no FTP on the athlete profile")
    powers = _values(samples, "power_w")
    if not powers:
        return _skip("no power recorded")

    seconds = {name: 0.0 for name, _, _ in POWER_ZONE_BANDS}
    for watts in powers:
        fraction = watts / ftp_w
        for name, low, high in POWER_ZONE_BANDS:
            if low <= fraction < high:
                seconds[name] += bucket_sec
                break
    total = sum(seconds.values()) or 1.0
    return {
        "status": "ok",
        "seconds": {z: round(s) for z, s in seconds.items()},
        "percent": {z: round(s / total * 100, 1) for z, s in seconds.items()},
    }


def stream_normalized_power(samples: list[dict], bucket_sec: int) -> float | None:
    """
    NP recomputed from the stream: fourth root of the mean fourth power of a 30 s
    rolling average. Garmin reports its own NP, but only for the whole activity;
    this one is derived from the same series the analysis reasons over.
    """
    powers = _values(samples, "power_w")
    if len(powers) < 2:
        return None
    window = max(1, round(NP_WINDOW_SEC / max(bucket_sec, 1)))
    rolling = [
        sum(powers[max(0, i - window + 1) : i + 1]) / len(powers[max(0, i - window + 1) : i + 1])
        for i in range(len(powers))
    ]
    return round(math.pow(sum(p**4 for p in rolling) / len(rolling), 0.25), 1)


def cadence_stability(samples: list[dict]) -> dict:
    """
    Mean and standard deviation of cadence. A high spread on a steady session is
    the signature of a stride or pedal stroke falling apart under fatigue.
    """
    values = _values(samples, "cadence")
    if len(values) < 4:
        return _skip("no cadence recorded")
    mean = _mean(values) or 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    sd = math.sqrt(variance)
    return {
        "status": "ok",
        "mean": round(mean, 1),
        "std_dev": round(sd, 2),
        "coefficient_of_variation_pct": round(sd / mean * 100, 1) if mean else None,
    }


def build_derived_metrics(
    workout: dict,
    stream: dict | None,
    profile: dict,
) -> dict:
    """
    Every derived metric for one workout, as one block for the Claude payload.
    Returns {"status": "unavailable", ...} when the activity has no stream at all
    (a manually entered session, or a Garmin fetch that came back empty).
    """
    if not stream or not stream.get("samples"):
        return {"status": "unavailable", "reason": "no sample stream for this activity"}

    samples = stream["samples"]
    bucket_sec = stream.get("bucket_sec") or 30
    discipline = workout.get("discipline") or "other"
    duration = workout.get("duration_sec")

    metrics: dict = {
        "status": "ok",
        "sample_bucket_sec": bucket_sec,
        "aerobic_decoupling": aerobic_decoupling(samples, duration),
        "half_splits": half_splits(samples, discipline),
        "cadence_stability": cadence_stability(samples),
    }
    if discipline == "bike":
        metrics["power_zones"] = power_zone_split(samples, profile.get("ftp_w"), bucket_sec)
        np_w = stream_normalized_power(samples, bucket_sec)
        if np_w is not None:
            metrics["normalized_power_from_stream_w"] = np_w
    return metrics
