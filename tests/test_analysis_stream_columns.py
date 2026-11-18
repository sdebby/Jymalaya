"""
The analysis stream goes to Claude as column names once plus value rows
(_columnar_samples in app/ai/prompt_builder.py), not one object per row.
"""

from app.ai.prompt_builder import _columnar_samples


def test_columns_follow_the_fixed_order_then_the_rest_sorted():
    out = _columnar_samples(
        [{"t": 0, "zeta": 1, "power_w": 200, "hr": 140, "alpha": 2}]
    )
    assert out["columns"] == ["t", "hr", "power_w", "alpha", "zeta"]
    assert out["rows"] == [[0, 140, 200, 2, 1]]


def test_a_value_a_row_lacks_is_null_and_unused_columns_are_left_out():
    out = _columnar_samples([{"t": 0, "hr": 140}, {"t": 30, "speed_mps": 3.1}])
    assert out["columns"] == ["t", "hr", "speed_mps"]
    assert out["rows"] == [[0, 140, None], [30, None, 3.1]]


def test_every_value_survives_the_round_trip():
    samples = [
        {"t": t, "hr": 130 + t / 30, "speed_mps": 3.0, "elevation_m": 40.5}
        for t in range(0, 300, 30)
    ]
    out = _columnar_samples(samples)
    back = [
        {c: v for c, v in zip(out["columns"], row) if v is not None}
        for row in out["rows"]
    ]
    assert back == samples
