"""Unit tests for Gold marts computed with Polars."""

from __future__ import annotations

import polars as pl

from pipelines.gold import airline_rankings, airport_metrics, delay_analysis, weather_impact


def _flights() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "flight_id": [f"F{i}" for i in range(6)],
            "airline_icao": ["DLH", "DLH", "BAW", "BAW", "BAW", "BAW"],
            "airline_name": ["Lufthansa"] * 2 + ["British"] * 4,
            "departure_icao": ["EDDF", "EDDF", "EGLL", "EGLL", "EGLL", "EGLL"],
            "arrival_icao": ["EGLL", "EGLL", "EDDF", "EDDF", "EDDF", "EDDF"],
            "scheduled_departure": [
                "2026-01-01T08:00:00+00:00",
                "2026-01-01T09:00:00+00:00",
                "2026-01-01T10:00:00+00:00",
                "2026-01-01T11:00:00+00:00",
                "2026-01-01T12:00:00+00:00",
                "2026-01-01T13:00:00+00:00",
            ],
            "scheduled_arrival": [
                "2026-01-01T09:30:00+00:00",
                "2026-01-01T10:30:00+00:00",
                "2026-01-01T11:30:00+00:00",
                "2026-01-01T12:30:00+00:00",
                "2026-01-01T13:30:00+00:00",
                "2026-01-01T14:30:00+00:00",
            ],
            "actual_departure": None,
            "actual_arrival": None,
            "status": ["landed"] * 5 + ["cancelled"],
            "delay_minutes": [0, 10, 20, 30, 40, 0],
            "cancelled": [False] * 5 + [True],
            "source": ["synthetic"] * 6,
        }
    )


def test_airport_metrics_excludes_cancelled():
    m = airport_metrics(_flights())
    assert m["total_flights"].sum() == 5
    assert "airport_icao" in m.columns


def test_airline_rankings_sorts_by_delay():
    r = airline_rankings(_flights())
    assert r["rank"].to_list() == [1, 2]  # DLH (5 avg) before BAW (30 avg)
    assert r["airline_icao"].to_list() == ["DLH", "BAW"]


def test_delay_analysis_groups_by_status():
    d = delay_analysis(_flights())
    assert set(d["status"].to_list()) == {"landed", "cancelled"}


def test_weather_impact_empty_without_weather():
    empty = pl.DataFrame()
    out = weather_impact(_flights(), empty)
    assert out.is_empty()


def test_weather_impact_joins_conditions():
    weather = pl.DataFrame(
        {
            "station_icao": ["EGLL", "EGLL", "EGLL"],
            "timestamp": [
                "2026-01-01T09:30:00+00:00",
                "2026-01-01T10:30:00+00:00",
                "2026-01-01T11:30:00+00:00",
            ],
            "condition": ["Clear", "Rain", "Clear"],
            "temperature_c": [10.0, 8.0, 11.0],
            "wind_speed_ms": [2.0, 8.0, 3.0],
            "visibility_m": [5000, 1200, 6000],
        }
    )
    out = weather_impact(_flights(), weather)
    assert out.height >= 1
    assert "weather_condition" in out.columns
