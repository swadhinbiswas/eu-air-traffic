"""Unit tests for the Aurora PostgreSQL publisher.

Postgres is not available in CI, so the tests cover the parts that carry the
logic and could silently regress: the type/value mapping, the generated upsert
SQL, and the publisher's table lifecycle via a small recording connection that
stands in for psycopg.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import duckdb
import pytest

from scripts.publish_aurora import (
    AuroraPublisher,
    _dsn,
    _pg_type,
    _pg_value,
    build_upsert_sql,
)


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn
        self.description = None
        self._result = None

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, params))
        if "INSERT INTO" in sql and "_sync_state" in sql and params:
            self.conn.state[params[0]] = params[1]
        self._result = self.conn.result_for(sql, params)
        self.description = [("value",)] if self._result is not None else None

    def fetchall(self):
        return self._result or []

    def close(self):
        pass


class FakeConn:
    """A psycopg-shaped connection that records SQL and fakes ``_sync_state``."""

    def __init__(self, *, table_exists: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.state: dict[str, str] = {}
        self.transactions = 0
        self._table_exists = table_exists

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def result_for(self, sql, params):
        norm = " ".join(sql.split())
        if "SELECT watermark FROM" in norm and params:
            return [(self.state.get(params[0], ""),)]
        if "to_regclass" in norm and params:
            return [(self._table_exists,)]
        return None

    def transaction(self):
        conn = self

        class _Transaction:
            def __enter__(self):
                conn.transactions += 1
                return conn

            def __exit__(self, *exc):
                return False

        return _Transaction()

    def close(self):
        pass


def _source_db(tmp_path, extra: str = "") -> object:
    path = tmp_path / "air_traffic.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE dim_airport AS SELECT 'EDDF' AS airport_icao, 'Frankfurt' AS name")
    if extra:
        con.execute(extra)
    con.close()
    return path


# ── pure helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("duck_type", "expected"),
    [
        ("BOOLEAN", "BOOLEAN"),
        ("TINYINT", "BIGINT"),
        ("BIGINT", "BIGINT"),
        ("INTEGER", "INTEGER"),
        ("DOUBLE", "DOUBLE PRECISION"),
        ("DECIMAL(10,2)", "NUMERIC"),
        ("TIMESTAMPTZ", "TIMESTAMPTZ"),
        ("DATE", "DATE"),
        ("BLOB", "BYTEA"),
        ("VARCHAR", "TEXT"),
        ("STRUCT(a INTEGER)", "TEXT"),
    ],
)
def test_pg_type_mapping(duck_type, expected):
    assert _pg_type(duck_type) == expected


def test_pg_value_passthrough_and_json():
    moment = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert _pg_value(moment) is moment
    assert _pg_value(True) is True
    assert _pg_value(Decimal("1.5")) == Decimal("1.5")
    assert _pg_value(None) is None
    assert _pg_value({"a": 1}) == '{"a": 1}'
    assert _pg_value([1, 2]) == "[1, 2]"


def test_build_upsert_sql():
    sql = build_upsert_sql(
        '"public"."fact_flights"',
        ["flight_id", "callsign", "delay_minutes"],
        ("flight_id",),
        2,
    )
    assert sql.count("(%s, %s, %s)") == 2
    assert 'ON CONFLICT ("flight_id") DO UPDATE SET' in sql
    assert '"callsign" = EXCLUDED."callsign"' in sql
    # The primary key is not updated onto itself.
    assert '"flight_id" = EXCLUDED."flight_id"' not in sql


def test_build_upsert_sql_without_non_key_columns():
    sql = build_upsert_sql("t", ["k"], ("k",), 1)
    assert sql.endswith("ON CONFLICT DO NOTHING")


def test_dsn_format():
    assert (
        _dsn("u", "p", "h", 5432, "db", "require") == "postgresql://u:p@h:5432/db?sslmode=require"
    )


# ── table lifecycle ──────────────────────────────────────────────────────────


def test_sync_static_publishes_then_skips_unchanged(tmp_path):
    db = _source_db(tmp_path)
    fake = FakeConn()
    publisher = AuroraPublisher(connection=fake, db_path=db)
    assert publisher._open_local()

    assert publisher._sync_static("dim_airport") == 1
    assert any("CREATE TABLE" in sql for sql, _ in fake.calls)

    fake.calls.clear()
    # Identical content: no create, no insert, no write cost.
    assert publisher._sync_static("dim_airport") == 0
    assert not any("CREATE TABLE" in sql for sql, _ in fake.calls)


def test_replace_table_swaps_in_one_transaction(tmp_path):
    db = _source_db(tmp_path)
    fake = FakeConn(table_exists=True)
    publisher = AuroraPublisher(connection=fake, db_path=db)
    assert publisher._open_local()

    publisher._replace_table(
        "dim_airport",
        [("airport_icao", "VARCHAR"), ("name", "VARCHAR")],
        ["airport_icao", "name"],
        [("EDDF", "Frankfurt")],
    )
    sqls = [sql for sql, _ in fake.calls]
    assert fake.transactions == 1
    assert any("RENAME TO" in sql for sql in sqls)
    assert any("DROP TABLE IF EXISTS" in sql for sql in sqls)


def test_sync_growing_uses_upsert(tmp_path):
    db = _source_db(
        tmp_path,
        "CREATE TABLE fact_flights (flight_id VARCHAR, callsign VARCHAR, "
        "collected_at TIMESTAMPTZ); "
        "INSERT INTO fact_flights VALUES "
        "('f1','DLH1', TIMESTAMPTZ '2026-01-01 10:00:00+00')",
    )
    fake = FakeConn()
    publisher = AuroraPublisher(connection=fake, db_path=db)
    assert publisher._open_local()

    assert publisher._sync_growing("fact_flights") == 1
    assert any("ON CONFLICT" in sql for sql, _ in fake.calls)
