"""Unit tests for the Turso (libSQL) publisher.

Runs against a local `file:` libSQL database — the same SQLite engine Turso
uses — so the DDL, upserts and watermark logic are exercised without a network.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from scripts import publish_turso
from scripts.publish_turso import TursoPublisher, parse_targets, publish

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
    from scripts.publish_turso import STATIC_REFRESH_SECONDS, STATIC_TABLES

    counts, target, source = _run(tmp_path)
    assert counts.get("dim_airport") == 1

    # Second run: identical content, so no static table may be rewritten.
    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    counts = publisher.run()
    assert "dim_airport" not in counts
    assert "gold_airport_metrics" not in counts
    # Large/slow tables keep a refresh floor so a rebuilt warehouse cannot
    # rewrite them every cycle; the served facts must not include positions.
    assert STATIC_REFRESH_SECONDS.get("dim_route", 0) > 0
    assert STATIC_REFRESH_SECONDS.get("gold_fuel_price_series", 0) > 0
    assert "fact_positions" not in STATIC_TABLES
    assert "fact_notams" not in STATIC_TABLES


def test_refresh_floor_suppresses_churn_on_large_tables(tmp_path):
    """A changed reference table is not rewritten until its floor elapses."""
    _, target, source = _run(tmp_path)

    src = duckdb.connect(str(source))
    src.execute("UPDATE dim_airport SET name = 'Frankfurt Flughafen'")
    src.close()

    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    counts = publisher.run()
    assert "dim_airport" not in counts

    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        # The serving copy still holds the previous name: the floor held.
        assert con.execute("SELECT name FROM dim_airport").rows[0][0] == "Frankfurt"
    finally:
        con.close()


def test_site_summary_serves_kpis_freshness_and_counts(tmp_path):
    """The one-row lookup the browser reads instead of scanning fact tables."""
    import json

    _, target, _ = _run(tmp_path)
    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        rows = dict(con.execute("SELECT key, payload_json FROM site_summary").rows)
        assert {"kpis", "freshness", "manifest", "catalog"} <= set(rows)

        kpis = json.loads(rows["kpis"])
        assert kpis["total_flights"] == 2
        assert kpis["airports"] == 1
        assert kpis["cancellation_rate"] == 0.0
        assert kpis["avg_delay_minutes"] == 4.0  # (3 + 5) / 2

        freshness = json.loads(rows["freshness"])
        assert freshness["total_flights"] == 2
        assert freshness["has_flights"] is True
        assert freshness["as_of"] is not None

        manifest = json.loads(rows["manifest"])
        assert manifest["flights"] == 2
        assert manifest["airports"] == 1

        catalog = json.loads(rows["catalog"])
        assert catalog["fact_flights"] == 2
        assert catalog["site_summary"] == 4
        assert "fact_positions" not in catalog
    finally:
        con.close()


def test_positions_and_notams_are_not_published(tmp_path):
    """Deprecated serving tables are dropped, not merely left stale."""
    counts, target, _ = _run(tmp_path)
    assert "fact_positions" not in counts
    assert "fact_notams" not in counts

    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        names = {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").rows
        }
        assert "fact_positions" not in names
        assert "fact_notams" not in names
    finally:
        con.close()


def test_epoch_ms_accepts_strings_and_datetimes():
    """collected_at is a VARCHAR in DuckDB; timestamp is a TIMESTAMPTZ."""
    from datetime import UTC, datetime

    from scripts.publish_turso import _epoch_ms

    expected = int(datetime(2026, 1, 1, 10, 0, tzinfo=UTC).timestamp() * 1000)
    assert _epoch_ms("2026-01-01T10:00:00+00:00") == expected
    assert _epoch_ms("2026-01-01T10:00:00Z") == expected
    assert _epoch_ms("2026-01-01T10:00:00") == expected  # naive treated as UTC
    assert _epoch_ms(datetime(2026, 1, 1, 10, 0, tzinfo=UTC)) == expected
    assert _epoch_ms("not-a-timestamp") == 0


def test_swapping_over_an_existing_table_leaves_no_leftovers(tmp_path, monkeypatch):
    """Republishing a changed static table replaces it without stranding copies."""
    # The per-table refresh floors are exercised elsewhere; this test covers
    # the swap itself, so let a changed hash publish immediately.
    monkeypatch.setattr("scripts.publish_turso.STATIC_REFRESH_SECONDS", {})
    _, target, source = _run(tmp_path)

    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        assert con.execute("SELECT name FROM dim_airport").rows[0][0] == "Frankfurt"
    finally:
        con.close()

    src = duckdb.connect(str(source))
    src.execute("UPDATE dim_airport SET name = 'Frankfurt Main'")
    src.close()

    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    counts = publisher.run()
    assert counts.get("dim_airport") == 1

    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        assert con.execute("SELECT name FROM dim_airport").rows[0][0] == "Frankfurt Main"
        leftovers = con.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%__new' OR name LIKE '%__old'"
        ).rows
        assert leftovers == []
    finally:
        con.close()


def test_swap_does_not_depend_on_the_column_probe(tmp_path, monkeypatch):
    """The name-collision bug.

    When the column probe failed, existence was read as False, the outgoing
    table was never moved aside, and the final rename failed with "there is
    already another table or index with this name". The swap now asks
    sqlite_master, so an unreliable column probe cannot cause it.
    """
    _, target, source = _run(tmp_path)

    monkeypatch.setattr(
        "scripts.publish_turso.TursoPublisher._sqlite_columns",
        lambda self, table: [],
    )
    monkeypatch.setattr("scripts.publish_turso.STATIC_REFRESH_SECONDS", {})
    src = duckdb.connect(str(source))
    src.execute("UPDATE dim_airport SET name = 'Frankfurt Hbf'")
    src.close()

    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    publisher.run()

    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        assert con.execute("SELECT name FROM dim_airport").rows[0][0] == "Frankfurt Hbf"
        leftovers = con.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%__new' OR name LIKE '%__old'"
        ).rows
        assert leftovers == []
    finally:
        con.close()


def test_table_exists_is_authoritative(tmp_path):
    _, target, source = _run(tmp_path)
    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    assert publisher._connect()
    try:
        assert publisher._table_exists("dim_airport") is True
        assert publisher._table_exists("not_a_table") is False
    finally:
        publisher._close()


def test_clear_name_handles_tables_and_stray_indexes(tmp_path):
    """A name can be occupied by an index; the swap fallback has to cope."""
    _, target, source = _run(tmp_path)
    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    assert publisher._connect()
    try:
        publisher.remote.execute("CREATE INDEX stranded_index ON weather (station_icao)")
        assert publisher._objects_named("stranded_index") == [("index", "stranded_index")]
        publisher._clear_name("stranded_index")
        assert publisher._objects_named("stranded_index") == []

        assert [kind for kind, _ in publisher._objects_named("dim_airport")] == ["table"]
        publisher._clear_name("dim_airport")
        assert publisher._objects_named("dim_airport") == []
    finally:
        publisher._close()


def test_swap_recovers_when_the_name_is_already_taken(tmp_path, monkeypatch):
    """If the batched swap is rejected, the loaded shadow still lands."""
    monkeypatch.setattr("scripts.publish_turso.STATIC_REFRESH_SECONDS", {})
    _, target, source = _run(tmp_path)

    src = duckdb.connect(str(source))
    src.execute("UPDATE dim_airport SET name = 'Frankfurt Sud'")
    src.close()

    publisher = TursoPublisher(url=f"file:{target}", token=None, db_path=source)
    assert publisher._connect()
    original_batch = publisher.remote.batch
    raised = {"done": False}

    def flaky_batch(statements):
        # Only the swap (the batch holding ALTER TABLE) is rejected, to mimic a
        # name collision; the row writes must keep working.
        if not raised["done"] and any("ALTER TABLE" in sql for sql, _ in statements):
            raised["done"] = True
            raise RuntimeError("Turso error: SQLite error: there is already another table")
        return original_batch(statements)

    monkeypatch.setattr(publisher.remote, "batch", flaky_batch)
    try:
        assert publisher._sync_static("dim_airport") == 1
    finally:
        publisher._close()
    assert raised["done"], "the swap batch was never rejected; test did not exercise the fallback"

    con = libsql_client.create_client_sync(url=f"file:{target}")
    try:
        assert con.execute("SELECT name FROM dim_airport").rows[0][0] == "Frankfurt Sud"
        leftovers = con.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%__new' OR name LIKE '%__old'"
        ).rows
        assert leftovers == []
    finally:
        con.close()


# ── multiple Turso targets ───────────────────────────────────────────────────


def _table_names(path) -> set[str]:
    con = libsql_client.create_client_sync(url=f"file:{path}")
    try:
        return {
            str(r[0])
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").rows
        }
    finally:
        con.close()


def _multi_target(tmp_path, monkeypatch, targets: list[dict]):
    source = tmp_path / "air_traffic.duckdb"
    _source_db(source)
    monkeypatch.setattr(publish_turso.settings, "turso_targets", json.dumps(targets))
    return source


def test_parse_targets_accepts_json_and_wildcards():
    targets = parse_targets(
        json.dumps(
            [
                {
                    "name": "one",
                    "url": "libsql://one.turso.io",
                    "token": "t1",
                    "tables": ["dim_airport", "fact_flights"],
                },
                {"name": "two", "url": "libsql://two.turso.io", "token": "t2"},
            ]
        )
    )
    assert [t.name for t in targets] == ["one", "two"]
    assert targets[0].owns("dim_airport")
    assert targets[0].owns("fact_flights")
    assert not targets[0].owns("weather")
    # No tables listed means every serving table (a full mirror).
    assert targets[1].owns("weather")
    assert targets[1].owns("site_summary")

    # An explicit url (CLI override, tests) is a single target owning everything.
    single = parse_targets(None, url="file:/tmp/x.db", token=None)
    assert len(single) == 1
    assert single[0].name == publish_turso.DEFAULT_TARGET_NAME
    assert single[0].owns("weather")


def test_parse_targets_rejects_bad_config():
    with pytest.raises(RuntimeError, match="not valid JSON"):
        parse_targets("{oops")
    with pytest.raises(RuntimeError, match="unknown tables"):
        parse_targets(
            json.dumps([{"name": "a", "url": "libsql://x", "tables": ["dim_airportz"]}])
        )
    with pytest.raises(RuntimeError, match="duplicate"):
        parse_targets(
            json.dumps(
                [
                    {"name": "a", "url": "libsql://x"},
                    {"name": "a", "url": "libsql://y"},
                ]
            )
        )
    with pytest.raises(RuntimeError, match="needs a url"):
        parse_targets(json.dumps([{"name": "a"}]))


def test_mirrored_tables_are_published_to_every_target(tmp_path, monkeypatch):
    """A table named by several targets is an independent copy in each."""
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    source = _multi_target(
        tmp_path,
        monkeypatch,
        [
            {
                "name": "eu-1",
                "url": f"file:{first}",
                "token": "",
                "tables": ["dim_airport", "fact_flights"],
            },
            {
                "name": "eu-2",
                "url": f"file:{second}",
                "token": "",
                "tables": ["dim_airport", "fact_flights"],
            },
        ],
    )
    counts = publish(db_path=source)

    # Both copies are written, so the aggregate counts them twice.
    assert counts["dim_airport"] == 2
    assert counts["fact_flights"] == 4
    for target in (first, second):
        con = libsql_client.create_client_sync(url=f"file:{target}")
        try:
            assert con.execute("SELECT COUNT(*) FROM dim_airport").rows[0][0] == 1
            assert con.execute("SELECT COUNT(*) FROM fact_flights").rows[0][0] == 2
            keys = {r[0] for r in con.execute("SELECT key FROM site_summary").rows}
            assert {"kpis", "catalog"} <= keys
        finally:
            con.close()


def test_partitioned_targets_only_receive_their_tables(tmp_path, monkeypatch):
    """Each target holds its assignment plus the always-published summary."""
    dims, facts = tmp_path / "dims.db", tmp_path / "facts.db"
    source = _multi_target(
        tmp_path,
        monkeypatch,
        [
            {"name": "dims", "url": f"file:{dims}", "tables": ["dim_airport"]},
            {"name": "facts", "url": f"file:{facts}", "tables": ["fact_flights"]},
        ],
    )
    publish(db_path=source)

    assert "dim_airport" in _table_names(dims)
    assert "fact_flights" not in _table_names(dims)
    assert "fact_flights" in _table_names(facts)
    assert "dim_airport" not in _table_names(facts)
    assert "site_summary" in _table_names(dims)
    assert "site_summary" in _table_names(facts)


def test_moving_a_table_drops_the_old_copy(tmp_path, monkeypatch):
    """Re-routing a table must not leave it lingering on its old target."""
    one, two = tmp_path / "one.db", tmp_path / "two.db"
    source = _multi_target(
        tmp_path,
        monkeypatch,
        [
            {"name": "one", "url": f"file:{one}", "tables": ["dim_airport"]},
            {"name": "two", "url": f"file:{two}", "tables": ["dim_airport"]},
        ],
    )
    publish(db_path=source)
    assert "dim_airport" in _table_names(one)
    assert "dim_airport" in _table_names(two)

    monkeypatch.setattr(
        publish_turso.settings,
        "turso_targets",
        json.dumps(
            [
                {"name": "one", "url": f"file:{one}", "tables": ["dim_airport"]},
                {
                    "name": "two",
                    "url": f"file:{two}",
                    "tables": ["gold_airport_metrics"],
                },
            ]
        ),
    )
    publish(db_path=source)

    assert "dim_airport" in _table_names(one)
    assert "dim_airport" not in _table_names(two)
    assert "gold_airport_metrics" in _table_names(two)


def test_target_selection_publishes_one_target_only(tmp_path, monkeypatch):
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    source = _multi_target(
        tmp_path,
        monkeypatch,
        [
            {"name": "eu-1", "url": f"file:{first}", "tables": ["*"]},
            {"name": "eu-2", "url": f"file:{second}", "tables": ["*"]},
        ],
    )
    counts = publish(db_path=source, only=["eu-2"])
    assert counts["dim_airport"] == 1
    assert "dim_airport" in _table_names(second)
    assert not first.exists()

    with pytest.raises(RuntimeError, match="unknown target"):
        publish(db_path=source, only=["eu-3"])


def test_one_failed_target_does_not_stop_the_others(tmp_path, monkeypatch):
    """An exhausted account must not stall its mirrors."""
    good = tmp_path / "good.db"
    source = _multi_target(
        tmp_path,
        monkeypatch,
        [
            {"name": "dead", "url": "libsql://dead.turso.io", "token": "x", "tables": ["*"]},
            {"name": "good", "url": f"file:{good}", "tables": ["*"]},
        ],
    )
    original_connect = publish_turso.TursoPublisher._connect

    def connect(self):
        if self.name == "dead":
            raise RuntimeError("quota exceeded")
        return original_connect(self)

    monkeypatch.setattr(publish_turso.TursoPublisher, "_connect", connect)
    counts = publish(db_path=source)
    assert counts["dim_airport"] == 1
    assert "dim_airport" in _table_names(good)


def test_all_targets_failing_raises(tmp_path, monkeypatch):
    _multi_target(
        tmp_path,
        monkeypatch,
        [
            {"name": "one", "url": "libsql://one.turso.io", "token": "x", "tables": ["*"]},
            {"name": "two", "url": "libsql://two.turso.io", "token": "y", "tables": ["*"]},
        ],
    )

    def connect(self):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(publish_turso.TursoPublisher, "_connect", connect)
    with pytest.raises(RuntimeError, match="every target failed"):
        publish(db_path=tmp_path / "air_traffic.duckdb")

