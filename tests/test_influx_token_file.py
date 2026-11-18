"""INFLUXDB_TOKEN_FILE: the single-container Jymalaya-C1 keeps its token in a file."""

from app.config import get_settings


def test_token_file_wins_over_env(tmp_path, monkeypatch):
    token_file = tmp_path / "app-token"
    token_file.write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("INFLUXDB_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("INFLUXDB_TOKEN", "from-a-copied-dev-env")

    assert get_settings().influx_token == "from-the-file"


def test_missing_or_empty_file_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("INFLUXDB_TOKEN", "from-env")

    monkeypatch.setenv("INFLUXDB_TOKEN_FILE", str(tmp_path / "not-there"))
    assert get_settings().influx_token == "from-env"

    empty = tmp_path / "empty"
    empty.write_text("  \n", encoding="utf-8")
    monkeypatch.setenv("INFLUXDB_TOKEN_FILE", str(empty))
    assert get_settings().influx_token == "from-env"


def test_no_file_configured_uses_env(monkeypatch):
    monkeypatch.delenv("INFLUXDB_TOKEN_FILE", raising=False)
    monkeypatch.setenv("INFLUXDB_TOKEN", "from-env")

    assert get_settings().influx_token == "from-env"
