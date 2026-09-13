"""Unit tests for the Turso (libSQL) publisher.

Runs against a local `file:` libSQL database — the same SQLite engine Turso
uses — so the DDL, upserts and watermark logic are exercised without a network.
"""

from __future__ import annotations

import duckdb
import pytest

from scripts.publish_turso import TursoPublisher

libsql_client = pytest.importorskip("libsql_client")


def _source_db(path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE dim_airport AS SELECT 'EDDF' AS airport_icao, 'Frankfurt' AS name, "
        "50.03 AS latitude_deg, 8.56 AS longitude_deg, 'large_airport' AS type, 380 AS score"
    )
    con.execute(
        "CREATE TABLE gold_airport_metrics AS SELECT 'EDDF' AS airport_icao, 120 AS total_flights, "
        "8.5 AS avg_delay_minutes, 0.82 AS on_time_rate"
    )
    con.execute(
        "CREATE TABLE fact_positions AS SELECT 'ABC123' AS icao24, 'DLH1' AS callsign, "
        "50.0 AS latitude, 8.0 AS longitude, 5040.0 AS co2_kg_per_hour"
    )
    con.execute(
        """
        CREATE TABLE fact_flights (
            flight_id VARCHAR, callsign VARCHAR, departure_icao VARCHAR, arrival_icao VARCHAR,
            status VARCHAR, delay_minutes DOUBLE, source VARCHAR, collected_at TIMESTAMPTZ,
            actual_arrival TIMESTAMPTZ
        )
        """
    )
    con.execute(
        "INSERT INTO fact_flights VALUES "
        "('f1','DLH1','EDDF','EGLL','landed',3.0,'opensky',"
        "TIMESTAMPTZ '2026-01-01 10:00:00+00', TIMESTAMPTZ '2026-01-01 11:00:00+00'),"
        "('f2','DLH2','EDDF','LFPG','landed',5.0,'opensky',"
        "TIMESTAMPTZ '2026-01-01 11:00:00+00', TIMESTAMPTZ '2026-01-01 12:00:00+00')"
    )
    con.execute(
        """
        CREATE TABLE weather (
            station_icao VARCHAR, timestamp TIMESTAMPTZ, temperature_c DOUBLE,
            condition VARCHAR, collected_at TIMESTAMPTZ
        )
        """
    )
    con.execute(
        "INSERT INTO weather VALUES "
        "('EDDF', TIMESTAMPTZ '2026-01-01 10:00:00+00', 5.0, 'VFR', TIMESTAMPTZ '2026-01-01 10:05:00+00')"
    )
    con.close()


def _run(tmp_path):
    source = tmp_path / "air_traffic.duckdb"
    target = tmp_path / "serving.db"
    if not source.exists():
        _source_db(source)
    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    counts = publisher.run()
    return counts, target, source


def test_tables_and_rows_are_published(tmp_path):
    counts, target, _ = _run(tmp_path)
    assert counts["dim_airport"] == 1
    assert counts["fact_flights"] == 2
    assert counts["weather"] == 1

    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        names = {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").rows
        }
        assert {"dim_airport", "gold_airport_metrics", "fact_flights", "weather"} <= names
        assert con.execute("SELECT COUNT(*) FROM fact_flights").rows[0][0] == 2
        assert con.execute("SELECT name FROM dim_airport").rows[0][0] == "Frankfurt"
    finally:
        con.close()


def test_incremental_sync_only_adds_new_rows(tmp_path):
    _, target, source = _run(tmp_path)

    # First run seeded the watermark; a second run must not re-write anything.
    _, target, source = _run(tmp_path)
    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        assert con.execute("SELECT COUNT(*) FROM fact_flights").rows[0][0] == 2
    finally:
        con.close()

    # One genuinely new flight (later actual_arrival) is the only row synced.
    src = duckdb.connect(str(source))
    src.execute(
        "INSERT INTO fact_flights VALUES "
        "('f3','DLH3','EDDF','LEMD','landed',1.0,'opensky',"
        "TIMESTAMPTZ '2026-01-01 13:00:00+00', TIMESTAMPTZ '2026-01-01 14:00:00+00')"
    )
    src.close()

    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    counts = publisher.run()
    assert counts.get("fact_flights") == 1

    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        assert con.execute("SELECT COUNT(*) FROM fact_flights").rows[0][0] == 3
        state = dict(con.execute("SELECT table_name, watermark FROM _sync_state").rows)
        assert int(state["fact_flights"].removeprefix("wm:")) > 0
    finally:
        con.close()


def test_row_update_is_upserted_not_duplicated(tmp_path):
    _, target, source = _run(tmp_path)
    src = duckdb.connect(str(source))
    # The same flight id, landing later: must replace, not duplicate.
    src.execute(
        "UPDATE fact_flights SET actual_arrival = TIMESTAMPTZ '2026-01-01 13:30:00+00', "
        "delay_minutes = 42.0, collected_at = TIMESTAMPTZ '2026-01-01 11:30:00+00' "
        "WHERE flight_id = 'f1'"
    )
    src.close()

    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    publisher.run()
    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        rows = con.execute("SELECT delay_minutes FROM fact_flights WHERE flight_id = 'f1'").rows
        assert len(rows) == 1
        assert rows[0][0] == 42.0
    finally:
        con.close()


def test_no_url_is_a_noop(tmp_path):
    publisher = TursoPublisher(url=None, token=None, db_path=tmp_path / "missing.duckdb")
    assert publisher.run() == {}


def test_http_args_follow_hrana_v2_encoding():
    """Integers are strings, floats must be JSON numbers (Turso rejects strings)."""
    from scripts.publish_turso import _TursoHttpClient

    assert _TursoHttpClient._arg(4) == {"type": "integer", "value": "4"}
    assert _TursoHttpClient._arg(4.5) == {"type": "float", "value": 4.5}
    assert _TursoHttpClient._arg(True) == {"type": "integer", "value": "1"}
    assert _TursoHttpClient._arg(None) == {"type": "null"}
    assert _TursoHttpClient._arg("EDDF") == {"type": "text", "value": "EDDF"}
    assert _TursoHttpClient._arg(float("nan")) == {"type": "null"}
    assert _TursoHttpClient._arg(float("inf")) == {"type": "null"}


def test_http_client_builds_pipeline_endpoint():
    from scripts.publish_turso import _TursoHttpClient

    client = _TursoHttpClient("libsql://db.turso.io", "token")
    try:
        assert client._endpoint == "https://db.turso.io/v2/pipeline"
    finally:
        client.close()


def test_unchanged_static_tables_are_reuploaded_only_on_change(tmp_path):
    from scripts.publish_turso import STATIC_REFRESH_SECONDS

    counts, target, source = _run(tmp_path)
    assert counts.get("dim_airport") == 1

    # Second run: identical content, so no static table may be rewritten.
    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    counts = publisher.run()
    assert "dim_airport" not in counts
    assert "gold_airport_metrics" not in counts
    # Positions are the deliberate exception: capped, not skipped forever.
    assert STATIC_REFRESH_SECONDS.get("fact_positions", 0) > 0
