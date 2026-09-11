"""Unit tests for the static web data bundle generator."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import duckdb
import polars as pl

from config.settings import Settings
from scripts import build_web_bundle as bwb


def _settings(tmp_path) -> Settings:
    warehouse = tmp_path / "warehouse"
    s = Settings(
        environment="test",
        mock_mode=True,
        project_root=tmp_path,
        warehouse_dir=warehouse,
        raw_dir=warehouse / "raw",
        bronze_dir=warehouse / "bronze",
        silver_dir=warehouse / "silver",
        gold_dir=warehouse / "gold",
        quarantine_dir=warehouse / "quarantine",
        checkpoint_dir=warehouse / "checkpoints",
        duckdb_path=warehouse / "air_traffic.duckdb",
        realtime_db_path=warehouse / "realtime.duckdb",
        rate_limit_delay_seconds=0.0,
    )
    s.ensure_directories()
    return s


def _seed_batch(s: Settings) -> None:
    with duckdb.connect(str(s.duckdb_path)) as con:
        con.execute(
            """
            CREATE TABLE fact_flights AS
            SELECT 'F1' flight_id, 'DLH' airline_icao, 'EDDF' departure_icao,
                   'EGLL' arrival_icao, TIMESTAMPTZ '2026-01-01 10:00:00+00' scheduled_departure,
                   'scheduled' status, 10.0 delay_minutes, FALSE cancelled
            UNION ALL SELECT 'F2','BAW','EGLL','EDDF',TIMESTAMPTZ '2026-01-01 12:00:00+00',
                   'cancelled', 0.0, TRUE
            """
        )
        con.execute(
            "CREATE TABLE dim_airline AS SELECT 'DLH' airline_icao, 'Lufthansa' airline_name"
        )
        con.execute(
            "CREATE TABLE dim_route AS SELECT 'EDDF' origin, 'EGLL' destination, 'DLH' airline, "
            "0 stops, 'A320' equipment, 700.0 distance_km"
        )
        con.execute(
            "CREATE TABLE dim_aircraft AS SELECT 'A320' type_icao, 'A32' type_iata, 'Airbus' manufacturer, "
            "'A320' AS \"family\", 'jet' engine, 180 capacity, 6000 range_km"
        )
        con.execute(
            "CREATE TABLE fact_emissions AS SELECT 'A320' aircraft_type, 2500 fuel_burn_liters_per_hour, "
            "5400.0 co2_kg_per_hour"
        )
        con.execute(
            "CREATE TABLE fact_notams AS SELECT 'N1' notam_id, 'EDDF' icao_location, 'A' notam_type, "
            "'msg' message, 1 qualification, '2026-01-01' valid_from, 0 valid_to, 'x' AS \"source\", 'y' collected_at"
        )
        con.execute(
            "CREATE VIEW gold_airport_metrics AS SELECT 'EDDF' airport_icao, 10 total_flights, "
            "5.0 avg_delay_minutes, 20.0 max_delay_minutes, 0.9 on_time_rate"
        )
        con.execute(
            "CREATE VIEW gold_airline_rankings AS SELECT 'DLH' airline_icao, 'Lufthansa' airline_name, "
            "10 total_flights, 4.0 avg_delay_minutes, 0.95 on_time_rate, 1 rank"
        )
        con.execute(
            "CREATE VIEW gold_delay_analysis AS SELECT 'scheduled' status, 5 flight_count, "
            "1.0 avg_delay_minutes, 0.0 min_delay_minutes, 5.0 max_delay_minutes"
        )
        con.execute(
            "CREATE VIEW gold_weather_impact AS SELECT 'Clear' weather_condition, 3 flight_count, "
            "2.0 avg_delay_minutes, 12.0 avg_temperature_c, 3.0 avg_wind_speed_ms"
        )
        con.execute(
            "CREATE VIEW gold_seasonal_trends AS SELECT '2026-01-01' flight_date, 12 hour_of_day, "
            "4 flight_count, 1.0 avg_delay_minutes"
        )
        con.execute(
            "CREATE VIEW gold_fuel_price_series AS SELECT '2026-01-01' date, 'EU' region, "
            "1.5 price_per_litre, 'EUR' currency"
        )


def _seed_realtime(s: Settings) -> None:
    with duckdb.connect(str(s.realtime_db_path)) as con:
        con.execute(
            "CREATE TABLE live_positions (icao24 VARCHAR, callsign VARCHAR, latitude DOUBLE, "
            "longitude DOUBLE, altitude DOUBLE, velocity DOUBLE, heading DOUBLE, "
            "vertical_rate DOUBLE, source VARCHAR, updated_at TIMESTAMPTZ)"
        )
        con.execute(
            "INSERT INTO live_positions VALUES "
            "('abc123','DLH1',50.0,8.0,35000.0,250.0,90.0,0.0,'adsb.lol', TIMESTAMPTZ '2026-01-01 10:00:00+00')"
        )
        con.execute(
            "CREATE TABLE latest_metar (icao VARCHAR, raw_text VARCHAR, temperature DOUBLE, "
            "wind_speed DOUBLE, wind_direction DOUBLE, visibility DOUBLE, cloud_cover VARCHAR, "
            "fetched_at TIMESTAMPTZ)"
        )
        con.execute(
            "INSERT INTO latest_metar VALUES ('EDDF','METAR',12.0,3.0,180.0,9999.0,'FEW', now())"
        )
        con.execute(
            "CREATE TABLE active_notams (id VARCHAR, icao_location VARCHAR, notam_type VARCHAR, "
            "message VARCHAR, valid_from TIMESTAMPTZ, valid_to TIMESTAMPTZ, fetched_at TIMESTAMPTZ)"
        )
        con.execute(
            "CREATE TABLE stream_health (source VARCHAR, last_success TIMESTAMPTZ, last_error VARCHAR, "
            "error_count INTEGER, total_fetches INTEGER, total_records INTEGER, is_healthy BOOLEAN, "
            "updated_at TIMESTAMPTZ)"
        )
        con.execute(
            "INSERT INTO stream_health VALUES ('positions', now(), NULL, 0, 1, 1, TRUE, now())"
        )


def _seed_reference(tmp_path) -> None:
    ingestion = tmp_path / "ingestion" / "airports"
    ingestion.mkdir(parents=True, exist_ok=True)
    (ingestion / "europe_airports.json").write_text(
        json.dumps(
            [
                {
                    "airport_id": 1,
                    "name": "Frankfurt",
                    "city": "Frankfurt",
                    "country": "Germany",
                    "iata": "FRA",
                    "icao": "EDDF",
                    "latitude": 50.03,
                    "longitude": 8.56,
                    "altitude_ft": 364,
                    "airport_type": "airport",
                }
            ]
        ),
        encoding="utf-8",
    )

    # Open-Meteo silver output for the weather bundle.
    weather_dir = tmp_path / "warehouse" / "silver" / "weather_forecast"
    weather_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "station_icao": ["EDDF", "EDDF"],
            "timestamp": [
                datetime(2026, 1, 1, 10, tzinfo=UTC),
                datetime(2026, 1, 1, 11, tzinfo=UTC),
            ],
            "is_forecast": [False, True],
            "temperature_c": [5.0, 6.0],
            "wind_speed_ms": [3.0, 4.0],
            "wind_direction_deg": [180, 190],
            "precipitation_mm": [0.0, 0.5],
            "humidity_pct": [80, None],
            "condition": ["Overcast", "Slight rain"],
            "weather_code": [3, 61],
        }
    ).write_parquet(weather_dir / "data.parquet")

    models = tmp_path / "dbt" / "models"
    (models / "staging").mkdir(parents=True, exist_ok=True)
    (models / "staging" / "stg_flights.sql").write_text(
        "{{ config(materialized='view') }}\nSELECT * FROM {{ source('raw', 'flights') }}",
        encoding="utf-8",
    )
    (models / "sources.yml").write_text(
        "sources:\n  - name: raw\n    tables:\n      - name: flights\n        description: raw flights\n",
        encoding="utf-8",
    )


def test_build_web_bundle(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    _seed_batch(s)
    _seed_realtime(s)
    _seed_reference(tmp_path)

    monkeypatch.setattr(bwb, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        bwb, "AIRPORT_REFERENCE", tmp_path / "ingestion" / "airports" / "europe_airports.json"
    )

    manifest = bwb.build_bundle(s)

    out = tmp_path / "web" / "public" / "data"
    for name in (
        "airports.json",
        "positions.json",
        "weather.json",
        "kpis.json",
        "analytics.json",
        "stories.json",
        "ops.json",
        "manifest.json",
    ):
        assert (out / name).exists(), f"{name} missing"

    assert manifest["counts"]["airports"] == 1
    assert manifest["counts"]["positions"] == 1
    assert manifest["counts"]["weather_stations"] == 1

    weather = json.loads((out / "weather.json").read_text())
    assert weather[0]["icao"] == "EDDF"
    assert weather[0]["temperature_c"] == 5.0
    assert weather[0]["name"] == "Frankfurt"
    assert len(weather[0]["hourly"]) == 1
    assert weather[0]["hourly"][0]["condition"] == "Slight rain"

    kpis = json.loads((out / "kpis.json").read_text())
    assert kpis["total_flights"] == 2

    catalog = json.loads((out / "analytics.json").read_text())["catalog"]
    assert any(n["id"] == "stg_flights" for n in catalog["lineage"]["nodes"])
    assert catalog["lineage"]["sources"][0]["id"] == "source.raw.flights"

    stories = json.loads((out / "stories.json").read_text())
    assert len(stories) >= 5
    assert all(story["title"] for story in stories)


def test_build_web_bundle_without_warehouse(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    _seed_reference(tmp_path)
    monkeypatch.setattr(bwb, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        bwb, "AIRPORT_REFERENCE", tmp_path / "ingestion" / "airports" / "europe_airports.json"
    )

    manifest = bwb.build_bundle(s)
    assert manifest["sources"]["batch_warehouse"] is False
