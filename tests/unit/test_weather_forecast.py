"""Unit tests for the Open-Meteo weather forecast collector."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from config.settings import Settings
from ingestion.weather.openmeteo import OpenMeteoCollector, weather_label


def _settings(tmp_path) -> Settings:
    s = Settings(
        environment="test",
        mock_mode=False,
        silver_dir=tmp_path / "silver",
        bronze_dir=tmp_path / "bronze",
        checkpoint_dir=tmp_path / "checkpoints",
    )
    s.ensure_directories()
    (s.silver_dir / "airports").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "ident": ["EDDF", "EGLL"],
            "latitude_deg": [50.03, 51.47],
            "longitude_deg": [8.56, -0.45],
        }
    ).write_parquet(s.silver_dir / "airports" / "airports.parquet")
    return s


def test_weather_label_maps_wmo_codes():
    assert weather_label(0) == "Clear"
    assert weather_label(3) == "Overcast"
    assert weather_label(95) == "Thunderstorm"
    assert weather_label(999) == "Unknown"
    assert weather_label(None) == "Unknown"


def test_rows_for_parses_current_and_forecast(tmp_path):
    collector = OpenMeteoCollector(_settings(tmp_path), max_airports=1)
    payload = {
        "current_units": {"wind_speed_10m": "m/s"},
        "current": {
            "time": "2026-01-01T10:00",
            "temperature_2m": 5.5,
            "relative_humidity_2m": 80,
            "precipitation": 0.0,
            "wind_speed_10m": 4.2,
            "wind_direction_10m": 180,
            "weather_code": 3,
        },
        "hourly": {
            "time": [
                "2020-01-01T00:00",  # far past → dropped
                datetime.now(UTC).strftime("%Y-%m-%dT%H:00"),
                (datetime.now(UTC).replace(microsecond=0)).strftime("%Y-%m-%dT%H:00"),
            ],
            "temperature_2m": [1.0, 6.0, 7.0],
            "precipitation": [0.0, 0.2, 0.1],
            "wind_speed_10m": [1.0, 5.0, 6.0],
            "wind_direction_10m": [90, 200, 210],
            "weather_code": [0, 61, 63],
        },
    }
    rows = collector._rows_for("EDDF", payload)
    assert rows
    current = [r for r in rows if not r["is_forecast"]]
    forecast = [r for r in rows if r["is_forecast"]]
    assert len(current) == 1
    assert current[0]["station_icao"] == "EDDF"
    assert current[0]["condition"] == "Overcast"
    assert current[0]["temperature_c"] == 5.5
    # The 2020 timestamp must be excluded.
    assert all("2020" not in str(r["timestamp"]) for r in forecast)


def test_mock_mode_is_deterministic(tmp_path):
    s = _settings(tmp_path)
    s.mock_mode = True
    collector = OpenMeteoCollector(s, max_airports=2)
    first = collector.fetch(datetime.now(UTC))
    second = collector.fetch(datetime.now(UTC))
    assert len(first) == 2 * (collector.FORECAST_HOURS + 1)
    assert len(first) == len(second)
    assert {r["station_icao"] for r in first} == {"EDDF", "EGLL"}
    assert all("weather_code" in r and "condition" in r for r in first)


def test_fetch_uses_batching(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    collector = OpenMeteoCollector(s, max_airports=2)
    calls: list[int] = []

    def fake_fetch(lats, lons):
        calls.append(len(lats))
        return [
            {
                "current": {"time": "2026-01-01T10:00", "temperature_2m": 5, "weather_code": 0},
                "hourly": {"time": []},
            }
            for _ in lats
        ]

    monkeypatch.setattr(OpenMeteoCollector, "_fetch_batch", staticmethod(fake_fetch))
    rows = collector.fetch(datetime.now(UTC))
    assert calls == [2]
    assert len(rows) == 2


def test_fetch_returns_empty_on_all_failures(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    collector = OpenMeteoCollector(s, max_airports=2)

    def boom(lats, lons):
        raise RuntimeError("network down")

    monkeypatch.setattr(OpenMeteoCollector, "_fetch_batch", staticmethod(boom))
    assert collector.fetch(datetime.now(UTC)) == []


@pytest.mark.parametrize("code,expected", [(0, "Clear"), (61, "Slight rain"), (95, "Thunderstorm")])
def test_weather_label_parametrized(code, expected):
    assert weather_label(code) == expected
