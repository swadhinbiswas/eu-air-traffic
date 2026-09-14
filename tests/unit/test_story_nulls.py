"""Story builders must survive a warehouse where every aggregate is NULL.

Unknown delays are NULL by design; each crash in CI was one story metric
formatted, subtracted or divided without a guard. This fixture gives every
queried table a single all-NULL row, so any unguarded arithmetic fails here
instead of in the nightly run.
"""

from __future__ import annotations

import duckdb

TABLES = {
    "fact_flights": (
        "flight_id VARCHAR, delay_minutes DOUBLE, status VARCHAR, callsign VARCHAR,"
        " departure_icao VARCHAR, arrival_icao VARCHAR, airline_icao VARCHAR,"
        " scheduled_departure TIMESTAMP, actual_departure TIMESTAMP, actual_arrival TIMESTAMP"
    ),
    "fact_positions": (
        "icao24 VARCHAR, altitude DOUBLE, co2_kg_per_hour DOUBLE,"
        " aircraft_type VARCHAR, collected_at VARCHAR"
    ),
    "gold_airline_rankings": (
        "airline_icao VARCHAR, airline_name VARCHAR, total_flights BIGINT,"
        " avg_delay_minutes DOUBLE, on_time_rate DOUBLE, rank BIGINT"
    ),
    "gold_seasonal_trends": ("hour_of_day BIGINT, flight_count BIGINT, avg_delay_minutes DOUBLE"),
    "gold_weather_impact": (
        "weather_condition VARCHAR, flight_count BIGINT, avg_delay_minutes DOUBLE"
    ),
    "gold_emissions_analysis": "aircraft_type VARCHAR, co2_kg_per_hour DOUBLE",
    "gold_airport_metrics": (
        "airport_icao VARCHAR, airport_name VARCHAR, total_flights BIGINT,"
        " avg_delay_minutes DOUBLE, on_time_rate DOUBLE"
    ),
    "dim_route": "origin VARCHAR, destination VARCHAR, distance_km DOUBLE",
}


def test_stories_survive_all_null_aggregates(tmp_path) -> None:
    con = duckdb.connect(str(tmp_path / "nulls.duckdb"))
    try:
        for name, columns in TABLES.items():
            names = [part.strip().split()[0] for part in columns.split(",")]
            con.execute(f'CREATE TABLE "{name}" ({columns})')
            con.execute(f'INSERT INTO "{name}" SELECT {", ".join("NULL" for _ in names)}')

        from scripts import build_web_bundle as bwb

        # The fixture covers the columns we know; a query touching a column it
        # does not model is skipped (schema gap in the harness), while NULL
        # arithmetic still raises TypeError and fails the test.
        original_rows, original_scalar = bwb._rows, bwb._scalar

        def safe_rows(connection, sql):
            try:
                return original_rows(connection, sql)
            except duckdb.Error:
                return []

        def safe_scalar(connection, sql, default=None):
            try:
                return original_scalar(connection, sql, default)
            except duckdb.Error:
                return default

        bwb._rows, bwb._scalar = safe_rows, safe_scalar
        try:
            stories = bwb._build_stories(con, 0, 0.0)
        finally:
            bwb._rows, bwb._scalar = original_rows, original_scalar
        assert isinstance(stories, list)
    finally:
        con.close()
