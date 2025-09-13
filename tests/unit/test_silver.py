"""Unit tests for the Silver transformation logic."""

from __future__ import annotations

import polars as pl

from pipelines.silver import _as_utc, transform_flights, transform_weather


def _flights() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "flight_id": ["LH100", "LH101", "LH100", ""],
            "callsign": ["DLH100", "DLH101", "DLH100", "DLH999"],
            "airline_icao": ["DLH", "DLH", "DLH", "DLH"],
            "airline_name": ["Lufthansa", "Lufthansa", "Lufthansa", "Lufthansa"],
            "departure_icao": ["EDDF", "EDDF", "EDDF", "EDDF"],
            "arrival_icao": ["EGLL", "EGLL", "EGLL", "EGLL"],
            "scheduled_departure": [
                "2026-01-01T10:00:00+00:00",
                "2026-01-01T12:00:00+00:00",
                "2026-01-01T10:00:00+00:00",
                "2026-01-01T09:00:00+00:00",
            ],
            "scheduled_arrival": [
                "2026-01-01T11:30:00+00:00",
                "2026-01-01T13:30:00+00:00",
                "2026-01-01T11:30:00+00:00",
                "2026-01-01T10:30:00+00:00",
            ],
            "actual_departure": ["2026-01-01T10:20:00+00:00"] * 4,
            "actual_arrival": ["2026-01-01T11:50:00+00:00"] * 4,
            "status": ["landed", "landed", "landed", "landed"],
            "delay_minutes": [20, 0, 20, -5],
            "cancelled": [False, False, False, False],
            "source": ["synthetic"] * 4,
        }
    )


def test_transform_flights_deduplicates_and_drops_bad():
    out = transform_flights(_flights())
    # LH100 deduped → 2 unique rows + bad empty flight removed.
    assert out["flight_id"].to_list() == ["LH100", "LH101"]
    # Delay clipped to >= 0.
    assert out.height == 2
    assert out["delay_minutes"].min() >= 0


def test_transform_flights_accepts_one_known_end():
    """OpenSky usually estimates only one end of a movement."""
    df = pl.DataFrame(
        {
            "flight_id": ["DEP1", "ARR1", "BAD1", "SAME"],
            "departure_icao": ["EDDF", None, None, "EDDF"],
            "arrival_icao": [None, "EGLL", None, "EDDF"],
        }
    )
    out = transform_flights(df)
    # Departure-only and arrival-only survive; nothing-known and same-end drop.
    assert set(out["flight_id"].to_list()) == {"DEP1", "ARR1"}


def test_transform_flights_prefers_complete_record_on_duplicate():
    df = pl.DataFrame(
        {
            "flight_id": ["A1", "A1"],
            "departure_icao": ["EDDF", "EDDF"],
            "arrival_icao": [None, "EGLL"],
        }
    )
    out = transform_flights(df)
    assert out.height == 1
    assert out["arrival_icao"].to_list() == ["EGLL"]


def test_transform_flights_utc_normalisation():
    out = transform_flights(_flights())
    first = out["scheduled_departure"].dt.replace_time_zone("UTC").to_list()[0]
    assert first is not None


def test_transform_weather_rejects_out_of_range():
    df = pl.DataFrame(
        {
            "station_icao": ["EDDF", "EGLL", "LFPG"],
            "timestamp": ["2026-01-01T10:00:00Z"] * 3,
            "temperature_c": [15.0, 200.0, None],
            "humidity_pct": [60, 60, 60],
            "wind_speed_ms": [3.0, 3.0, 3.0],
            "visibility_m": [1000, 1000, 1000],
            "condition": ["Clear", "Clear", "Clear"],
        }
    )
    out = transform_weather(df)
    assert out["station_icao"].to_list() == ["EDDF"]


def test_as_utc_handles_naive_and_aware():
    df = pl.DataFrame({"ts": ["2026-01-01T10:00:00+01:00", "2026-01-01T09:00:00", "2026-01-01"]})
    result = df.with_columns(_as_utc("ts").alias("utc"))
    parsed = result["utc"].dt.replace_time_zone("UTC").to_list()
    assert all(v is not None for v in parsed)
    assert parsed[0] == parsed[1]


def test_silver_incremental_only_processes_new_bronze(tmp_path):
    """A second run with no new Bronze must do nothing; a new file is picked up."""
    from config.settings import Settings
    from pipelines import silver

    warehouse = tmp_path / "warehouse"
    s = Settings(
        environment="test",
        mock_mode=True,
        motherduck_token=None,
        warehouse_dir=warehouse,
        bronze_dir=warehouse / "bronze",
        silver_dir=warehouse / "silver",
        quarantine_dir=warehouse / "quarantine",
        checkpoint_dir=warehouse / "checkpoints",
        duckdb_path=warehouse / "a.duckdb",
        rate_limit_delay_seconds=0.0,
    )
    s.ensure_directories()
    bdir = s.bronze_dir / "parquet" / "positions"
    bdir.mkdir(parents=True)

    pl.DataFrame(
        [
            {
                "icao24": "AAA",
                "callsign": "DLH1",
                "aircraft_type": "A320",
                "latitude": 50.0,
                "longitude": 8.0,
            }
        ]
    ).write_parquet(bdir / "positions_1.parquet")
    pl.DataFrame(
        [
            {
                "icao24": "BBB",
                "callsign": "FDX9",
                "aircraft_type": "B77F",
                "latitude": 51.0,
                "longitude": 9.0,
            }
        ]
    ).write_parquet(bdir / "positions_2.parquet")

    assert silver.run("positions", s) == 2
    # Nothing new → nothing processed.
    assert silver.run("positions", s) == 0
    # One new file → exactly one row processed.
    pl.DataFrame(
        [
            {
                "icao24": "CCC",
                "callsign": "RCH1",
                "aircraft_type": "C17",
                "latitude": 52.0,
                "longitude": 10.0,
            }
        ]
    ).write_parquet(bdir / "positions_3.parquet")
    assert silver.run("positions", s) == 1

    out = pl.read_parquet(s.silver_dir / "positions" / "data.parquet")
    assert sorted(out["aircraft_class"].to_list()) == ["cargo", "military", "passenger"]


def test_transform_flights_normalises_status_vocabulary():
    df = pl.DataFrame(
        {
            "flight_id": ["A", "B"],
            "departure_icao": ["EDDF", "EDDF"],
            "arrival_icao": ["EGLL", "EGLL"],
            "status": ["en_route", "EN ROUTE"],
        }
    )
    out = transform_flights(df)
    assert set(out["status"].to_list()) == {"en-route"}
