"""A workout sent to the coach chat brings its full recorded data, compactly."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

import main
from app.ai import workout_context as wc
from app.metrics.analysis import build_derived_metrics
from app.routers import coach as coach_router

RUN = {
    "activity_id": "111",
    "date": "2026-11-20",
    "discipline": "run",
    "title": "Tempo",
    "duration_sec": 3000,
    "distance_m": 10000,
    "tss": 70,
    "tss_method": "ngp",
    "intensity_factor": 0.9,
    "avg_hr": 150,
    "max_hr": 170,
    "avg_pace_sec_per_km": 300,
    "ngp_sec_per_km": 295,
    "avg_cadence": 176,
    "zones_time_sec": json.dumps({"Z2": 1200, "Z3": 1800}),
    "aerobic_te": 3.4,
    "anaerobic_te": 0.0,
    "te_label": "LACTATE_THRESHOLD",
    "rpe": 6.0,
    "feel": 0.0,
}

RIDE = {
    "activity_id": "222",
    "date": "2026-11-21",
    "discipline": "bike",
    "duration_sec": 1800,
    "tss": 40,
    "avg_power": 180,
}


def _stream(n=6, **extra):
    samples = []
    for i in range(n):
        s = {"t": i * 30, "hr": 140 + i, "speed_mps": 3.333, "cadence": 176, "elevation_m": 20.4, "distance_m": i * 100.0}
        s.update(extra)
        samples.append(s)
    return {"bucket_sec": 30, "sample_count": n, "samples": samples}


def _block(workout, stream, **kw):
    return wc.format_workout_block(
        workout,
        stream=stream,
        laps=kw.get("laps", []),
        planned=kw.get("planned"),
        derived=build_derived_metrics(workout, stream, {"ftp_w": 250}),
        power_zones=kw.get("power_zones"),
        analysis=kw.get("analysis"),
    )


def test_run_block_carries_everything_the_modal_shows():
    stream = _stream()
    stream["samples"][2]["speed_mps"] = 0.2  # a stop
    del stream["samples"][3]["hr"]
    text = _block(
        RUN,
        stream,
        laps=[{"lap": 1, "duration_sec": 1500, "avg_speed_mps": 3.333, "avg_hr": 148}, {"lap": 2, "duration_sec": 1500, "avg_hr": 152}],
        planned={"name": "Tempo", "steps": [{"type": "interval"}]},
        analysis={
            "plan_adherence": {"verdict": "on target", "detail": "Held the pace."},
            "what_went_well": ["Even pacing"],
            "what_to_improve": ["Longer warm-up"],
            "flags": ["HR drift"],
            "form_context": "Form is low. Ease off tomorrow.",
        },
    )
    assert "### Workout 111: Friday 2026-11-20, run" in text
    assert "pace avg 5:00/km" in text and "NGP 4:55/km" in text
    assert "TSS 70 (ngp)" in text
    assert "fatigue (ATL) +10.0" in text
    assert "aerobic 3.4, anaerobic 0.0, primary benefit lactate threshold" in text
    assert "RPE 6/10, feel Very Weak" in text  # 0 is an answer, not a blank
    assert "Z2 20.0, Z3 30.0" in text
    assert 'planned workout it was run against: {"name":"Tempo"' in text
    # The laps have no distance, so that column is left out.
    assert "lap,min,hr,pace/km\n1,25.0,148,5:00\n2,25.0,152," in text
    # Streams: graph units, empty columns dropped, a blank for a stop or a gap.
    assert "min,pace/km,hr,cad,elev,km" in text
    assert "0.0,5:00,140,176,20,0.00" in text
    assert "1.0,,142,176,20,0.20" in text
    assert "1.5,5:00,,176,20,0.30" in text
    assert "- execution vs plan: on target Held the pace." in text
    assert "- against current form: Ease off tomorrow." in text
    assert "- flag: HR drift" in text
    assert "speed_mps" not in text and "lat," not in text and "lon," not in text


def test_ride_block_uses_kmh_and_power_zones():
    text = _block(RIDE, _stream(power_w=200.4), power_zones={"Z1": 600, "Z2": 1200})
    assert "min,kmh,hr,w,cad,elev,km" in text
    assert "0.0,12.0,140,200,176,20,0.00" in text
    assert "time in power zones (% FTP), min: Z1 10.0, Z2 20.0" in text
    assert "NP from the stream: 200 W" in text
    assert "kmh speed in km/h" in text


def test_a_workout_without_samples_still_gets_its_summary():
    text = _block(RIDE, None)
    assert "summary: 30 min" in text
    assert text.endswith("recorded streams: none stored")


class _Repo:
    def close(self):
        pass


def test_context_skips_unknown_and_failing_workouts(monkeypatch):
    monkeypatch.setattr(wc, "InfluxRepo", _Repo)
    monkeypatch.setattr(wc, "get_athlete_profile", lambda: {"ftp_w": 250})
    stored = {"111": RUN, "222": RIDE}

    def load(repo, activity_id):
        if activity_id == "boom":
            raise RuntimeError("influx down")
        return stored.get(activity_id)

    monkeypatch.setattr(wc, "load_stored_workout", load)
    monkeypatch.setattr(wc, "stored_samples", lambda repo, a: [] if a == "222" else [{"t": 0, "hr": 140}, {"t": 30, "hr": 150}])
    monkeypatch.setattr(wc, "stored_laps", lambda repo, a: [])
    monkeypatch.setattr(wc, "stored_planned", lambda repo, a: None)
    text = wc.attached_workouts_context(["111", "missing", "boom", "222"])
    # At most three ids are read, and the bad ones are left out.
    assert text.startswith("## Attached workouts")
    assert "### Workout 111" in text and "### Workout 222" not in text
    assert wc.attached_workouts_context(["missing"]) == ""
    assert wc.attached_workouts_context([]) == ""


def _post(monkeypatch, body):
    seen = {}

    def fake_reply(message, history, context, *, tools=True):
        seen["context"] = context
        return "Fine.", False, False

    monkeypatch.setattr(coach_router, "coach_reply", fake_reply)
    monkeypatch.setattr(coach_router, "build_coach_context", lambda: "ATHLETE CONTEXT")
    monkeypatch.setattr(coach_router, "build_general_coach_context", lambda: "GENERAL CONTEXT")
    monkeypatch.setattr(coach_router, "attached_workouts_context", lambda ids: f"WORKOUTS {','.join(ids)}")
    client = TestClient(main.app, base_url="http://testserver/api")
    return client.post("/coach", json=body), seen


def test_route_adds_the_attached_workouts(monkeypatch):
    res, seen = _post(monkeypatch, {"message": "How was it?", "workout_ids": ["111", "222_run"]})
    assert res.status_code == 200
    assert seen["context"].startswith("ATHLETE CONTEXT\n\nWORKOUTS 111,222_run")


def test_route_sends_no_workout_with_the_context_off(monkeypatch):
    res, seen = _post(monkeypatch, {"message": "q", "include_context": False, "workout_ids": ["111"]})
    assert res.status_code == 200
    assert "WORKOUTS" not in seen["context"]


def test_route_rejects_a_bad_id_or_too_many(monkeypatch):
    assert _post(monkeypatch, {"message": "q", "workout_ids": ["../x"]})[0].status_code == 422
    assert _post(monkeypatch, {"message": "q", "workout_ids": ["1", "2", "3", "4"]})[0].status_code == 422
