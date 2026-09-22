"""Publish the Gold/serving tables to Aurora PostgreSQL for the dashboard.

This is the AWS counterpart to :mod:`scripts.publish_turso`. The design is the
same because the constraints are the same — the dashboard needs a small,
read-only copy it can query safely — and Amazon Aurora supplies the one thing
Turso supplied that MotherDuck could not: **a credential the browser can hold
that cannot write**. The task role publishes to the cluster writer; the
dashboard reads the reader endpoint with a read-only database user.

What is preserved from the Turso publisher, deliberately:

* Small tables are replaced only when their content hash changes **and** a
  per-table refresh floor has elapsed, so slow reference data cannot churn.
* The two growing fact tables are synced **incrementally** against a watermark
  kept in ``_sync_state``; each row is written once.
* Static tables load into a shadow table and swap **inside one transaction**,
  so a reader never sees the table empty and a crash leaves the old copy intact.
* ``site_summary`` is precomputed here from the local warehouse, so a browser
  poll reads one row instead of scanning the fact tables.

Differences from Turso: Postgres namespaces are per-schema (the swap uses
``ALTER TABLE … RENAME`` inside a transaction, not a sqlite pipeline), upserts
use ``ON CONFLICT … DO UPDATE``, and the password can be a short-lived **RDS
IAM token** (``AURORA_IAM_AUTH=true``) instead of a stored secret.

    python -m scripts.publish_aurora
    python -m scripts.publish_aurora --dsn postgresql://… --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb

from config.logging import logger
from config.settings import settings
from scripts.publish_turso import (
    DEPRECATED_TABLES,
    GROWING_RESET_LOOKBACK_MS,
    GROWING_TABLES,
    MAX_ROWS_PER_SYNC,
    STATIC_REFRESH_SECONDS,
    STATIC_TABLES,
    _epoch_ms,
)

# Rows per multi-row INSERT. Postgres accepts 65535 bind parameters per
# statement, so a wide table (say 40 columns) can carry ~1500 rows; 200 keeps
# well clear of the limit and of the wire-size preference.
MULTI_ROW_STATEMENT_ROWS = 200


def _pg_type(duck_type: str) -> str:
    """Map a DuckDB column type onto its Postgres equivalent."""
    t = (duck_type or "").upper()
    if t.startswith(("STRUCT", "LIST", "MAP", "UNION", "ARRAY")):
        # Nested types have no direct Postgres type; they are stored as JSON text.
        return "TEXT"
    if "TIMESTAMP" in t or "DATETIME" in t:
        return "TIMESTAMPTZ"
    if t.startswith("BOOL"):
        return "BOOLEAN"
    if "TINYINT" in t or "SMALLINT" in t or "BIGINT" in t or "HUGEINT" in t:
        return "BIGINT"
    if "INT" in t:
        return "INTEGER"
    if "DOUBLE" in t or "FLOAT" in t or "REAL" in t:
        return "DOUBLE PRECISION"
    if "DECIMAL" in t or "NUMERIC" in t:
        return "NUMERIC"
    if t.startswith("DATE"):
        return "DATE"
    if "BLOB" in t or "BYTEA" in t:
        return "BYTEA"
    if t.startswith("JSON"):
        return "JSONB"
    return "TEXT"


def _pg_value(value: Any) -> Any:
    """Adapt a DuckDB value for psycopg.

    Postgres is typed, so datetimes, dates, decimals and booleans are passed
    through for the driver to adapt rather than stringified. Only containers
    (DuckDB ``LIST``/``STRUCT``/``MAP``) have no direct driver type and are
    JSON-encoded.
    """
    if value is None or isinstance(value, (bool, int, float, str, bytes, datetime, date, Decimal)):
        return value
    return json.dumps(value, default=str)


def build_upsert_sql(
    table: str, columns: list[str], primary_key: tuple[str, ...], rows: int
) -> str:
    """A multi-row ``INSERT … ON CONFLICT … DO UPDATE`` for ``rows`` rows.

    ``table`` is a ready SQL identifier (or a qualified name such as
    ``"public"."fact_flights"``); ``columns`` are quoted here.
    """
    column_list = ", ".join(f'"{c}"' for c in columns)
    placeholder = "(" + ", ".join("%s" for _ in columns) + ")"
    values = ", ".join(placeholder for _ in range(rows))
    updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in columns if c not in primary_key)
    key = ", ".join(f'"{c}"' for c in primary_key)
    conflict = (
        f"ON CONFLICT ({key}) DO UPDATE SET {updates}" if updates else "ON CONFLICT DO NOTHING"
    )
    return f"INSERT INTO {table} ({column_list}) VALUES {values} {conflict}"


def _dsn(user: str, password: str, host: str, port: int, database: str, sslmode: str) -> str:
    return f"postgresql://{user}:{password}@{host}:{port}/{database}?sslmode={sslmode}"


class AuroraPublisher:
    """Sync the local warehouse into one Aurora PostgreSQL database.

    The ``connection`` argument exists for tests: pass any DBAPI/psycopg-shaped
    connection (or ``None`` to connect from ``dsn``/settings). No production
    call path uses it.
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        db_path: Path | None = None,
        connection: Any = None,
        name: str = "serving",
    ) -> None:
        self.name = name
        self.dsn = dsn or settings.database_url
        self.db_path = Path(db_path or settings.duckdb_path)
        self._connection = connection
        self.conn: Any = connection
        self.local: duckdb.DuckDBPyConnection | None = None
        self.counts: dict[str, int] = {}

    # ── connections ────────────────────────────────────────────────────────
    def _iam_auth_token(self) -> str:
        """A short-lived RDS token, so no password is stored in the task."""
        import boto3

        parsed = urlparse(self.dsn or "")
        host = parsed.hostname or os.environ.get("PGHOST")
        user = parsed.username or os.environ.get("PGUSER")
        port = parsed.port or int(os.environ.get("PGPORT", "5432"))
        if not host or not user:
            raise RuntimeError("[aurora] IAM auth needs a host and user (DSN or PGHOST/PGUSER)")
        client = boto3.client("rds", region_name=settings.aws_region)
        return client.generate_db_auth_token(
            DBHostname=host, Port=port, DBUsername=user, Region=settings.aws_region
        )

    def _connect(self) -> bool:
        if self._connection is not None:
            self.conn = self._connection
            return True
        # No DSN is fine: an empty conninfo makes libpq fall back to the standard
        # PG* environment variables (PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD),
        # which is exactly how the ECS task is wired — the password comes from
        # the RDS-managed secret via the container `secrets` block.
        if not self.dsn and not os.environ.get("PGHOST"):
            logger.warning("[aurora] %s: no DSN or PGHOST configured — skipping", self.name)
            return False
        if not self.db_path.exists():
            logger.error("[aurora] %s: local warehouse missing: %s", self.name, self.db_path)
            return False
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "[aurora] psycopg is required — install with `uv sync --extra postgres`"
            ) from exc
        # Only pass a password when IAM auth is on; otherwise we must not pass
        # password=None, which would shadow PGPASSWORD from the environment.
        kwargs: dict[str, Any] = {"sslmode": settings.pg_sslmode}
        if settings.aurora_iam_auth:
            kwargs["password"] = self._iam_auth_token()
        self.conn = psycopg.connect(self.dsn or "", **kwargs)
        return True

    def _close(self) -> None:
        if self.local is not None:
            self.local.close()
            self.local = None
        if self.conn is not None and self._connection is None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
        self.conn = None

    def _open_local(self) -> bool:
        if self.local is not None:
            return True
        if not self.db_path.exists():
            return False
        self.local = duckdb.connect(str(self.db_path), read_only=True)
        return True

    def _execute(self, sql: str, params: Any = None) -> Any:
        assert self.conn is not None
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            if cur.description is not None:
                return cur.fetchall()
            return None
        finally:
            cur.close()

    # ── schema ─────────────────────────────────────────────────────────────
    def _columns(self, table: str) -> list[tuple[str, str]]:
        assert self.local is not None
        try:
            rows = self.local.execute(f'DESCRIBE "{table}"').fetchall()
        except duckdb.Error:
            return []
        return [(str(r[0]), str(r[1])) for r in rows]

    def _table_exists(self, table: str) -> bool:
        rows = self._execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"{settings.pg_schema}.{table}",)
        )
        return bool(rows and rows[0][0])

    def _pg_columns(self, table: str) -> list[str]:
        rows = self._execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (settings.pg_schema, table),
        )
        return [str(r[0]) for r in (rows or [])]

    def _qualified(self, table: str) -> str:
        return f'"{settings.pg_schema}"."{table}"'

    def _ensure_table(
        self, table: str, columns: list[tuple[str, str]], replace: bool = False
    ) -> bool:
        """Return True only when an existing table was recreated (schema change).

        A brand-new table must keep the caller's initial watermark of zero so
        the whole table seeds; recreating one that already has data invalidates
        the stored watermark and needs a re-seed window.
        """
        wanted = [c for c, _ in columns]
        if not self._table_exists(table):
            self._create_table(table, columns)
            return False
        existing = self._pg_columns(table)
        if replace or existing != wanted:
            self._execute(f"DROP TABLE IF EXISTS {self._qualified(table)}")
            self._create_table(table, columns)
            return True
        return False

    def _create_table(self, table: str, columns: list[tuple[str, str]]) -> None:
        defs = ", ".join(f'"{c}" {_pg_type(t)}' for c, t in columns)
        primary = GROWING_TABLES.get(table, ((), ""))[0]
        if primary:
            defs += ", PRIMARY KEY (" + ", ".join(f'"{c}"' for c in primary) + ")"
        self._execute(f"CREATE TABLE {self._qualified(table)} ({defs})")

    # ── state ──────────────────────────────────────────────────────────────
    def _ensure_state(self) -> None:
        self._execute(
            "CREATE TABLE IF NOT EXISTS "
            f"{self._qualified('_sync_state')} "
            "(table_name TEXT PRIMARY KEY, watermark TEXT)"
        )

    def _state_get(self, table: str) -> str:
        try:
            rows = self._execute(
                f"SELECT watermark FROM {self._qualified('_sync_state')} WHERE table_name = %s",
                (table,),
            )
            return str(rows[0][0]) if rows else ""
        except Exception:  # noqa: BLE001 - created lazily by the caller
            return ""

    def _state_set(self, table: str, value: str) -> None:
        self._execute(
            f"INSERT INTO {self._qualified('_sync_state')} (table_name, watermark) "
            "VALUES (%s, %s) ON CONFLICT (table_name) DO UPDATE SET watermark = EXCLUDED.watermark",
            (table, value),
        )

    def _watermark(self, table: str) -> str:
        value = self._state_get(table)
        if value.startswith("wm:"):
            return value[3:]
        return value if value.isdigit() else "0"

    def _set_watermark(self, table: str, watermark: str) -> None:
        self._state_set(table, f"wm:{watermark}")

    def _static_state(self, table: str) -> tuple[str, int]:
        value = self._state_get(table)
        if not value.startswith("static:"):
            return "", 0
        parts = value.split(":", 2)
        if len(parts) != 3:
            return "", 0
        try:
            return parts[2], int(parts[1])
        except ValueError:
            return "", 0

    # ── writes ─────────────────────────────────────────────────────────────
    def _write_rows(self, table: str, columns: list[str], rows: list[tuple[Any, ...]]) -> int:
        if not rows:
            return 0
        column_list = ", ".join(f'"{c}"' for c in columns)
        placeholder = "(" + ", ".join("%s" for _ in columns) + ")"
        written = 0
        for start in range(0, len(rows), MULTI_ROW_STATEMENT_ROWS):
            group = rows[start : start + MULTI_ROW_STATEMENT_ROWS]
            values = ", ".join(placeholder for _ in group)
            params = [value for row in group for value in row]
            self._execute(
                f"INSERT INTO {self._qualified(table)} ({column_list}) VALUES {values}", params
            )
            written += len(group)
        return written

    def _replace_table(
        self,
        table: str,
        columns: list[tuple[str, str]],
        names: list[str],
        rows: list[tuple[Any, ...]],
    ) -> None:
        """Load a shadow table and swap it in, all inside one transaction.

        Readers on the Aurora reader endpoint never observe an empty table: the
        rename is atomic, and a failure before the commit leaves the outgoing
        table exactly where it was.
        """
        shadow, previous = f"{table}__new", f"{table}__old"
        defs = ", ".join(f'"{c}" {_pg_type(t)}' for c, t in columns)
        with self.conn.transaction():  # type: ignore[union-attr]
            self._execute(f"DROP TABLE IF EXISTS {self._qualified(shadow)}")
            self._execute(f"CREATE TABLE {self._qualified(shadow)} ({defs})")
            self._write_rows(shadow, names, rows)
            self._execute(f"DROP TABLE IF EXISTS {self._qualified(previous)}")
            if self._table_exists(table):
                self._execute(f'ALTER TABLE {self._qualified(table)} RENAME TO "{previous}"')
            self._execute(f'ALTER TABLE {self._qualified(shadow)} RENAME TO "{table}"')
            self._execute(f"DROP TABLE IF EXISTS {self._qualified(previous)}")

    def _sync_static(self, table: str) -> int:
        assert self.local is not None
        columns = self._columns(table)
        if not columns:
            logger.warning("[aurora] local table %s missing — skipped", table)
            return 0
        names = [c for c, _ in columns]
        select = ", ".join(f'"{c}"' for c in names)
        raw = self.local.execute(f'SELECT {select} FROM "{table}"').fetchall()
        rows = [tuple(_pg_value(v) for v in record) for record in raw]
        digest = hashlib.sha256(
            json.dumps(rows, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        previous_digest, last_sync_ms = self._static_state(table)
        if previous_digest == digest:
            return 0
        now_ms = int(time.time() * 1000)
        refresh_ms = STATIC_REFRESH_SECONDS.get(table, 0) * 1000
        if refresh_ms and now_ms - last_sync_ms < refresh_ms:
            return 0
        self._replace_table(table, columns, names, rows)
        self._state_set(table, f"static:{now_ms}:{digest}")
        return len(rows)

    def _sync_growing(self, table: str) -> int:
        assert self.local is not None
        primary, watermark_column = GROWING_TABLES[table]
        columns = self._columns(table)
        if not columns:
            logger.warning("[aurora] local table %s missing — skipped", table)
            return 0
        recreated = self._ensure_table(table, columns)
        if recreated:
            lookback = GROWING_RESET_LOOKBACK_MS.get(table, 24 * 3_600_000)
            self._set_watermark(table, str(int(time.time() * 1000) - lookback))
            logger.warning("[aurora] %s was created/recreated — re-seeding recent rows", table)
        names = [c for c, _ in columns]
        select = ", ".join(f'"{c}"' for c in names)
        watermark = self._watermark(table) or "0"
        raw = self.local.execute(
            f'SELECT {select} FROM "{table}" '
            f'WHERE "{watermark_column}" IS NOT NULL '
            f'AND epoch_ms(CAST("{watermark_column}" AS TIMESTAMPTZ)) > CAST(? AS BIGINT) '
            f'ORDER BY "{watermark_column}" LIMIT {MAX_ROWS_PER_SYNC}',
            [watermark],
        ).fetchall()
        if not raw:
            return 0

        rows = [tuple(_pg_value(v) for v in record) for record in raw]
        written = 0
        for start in range(0, len(rows), MULTI_ROW_STATEMENT_ROWS):
            group = rows[start : start + MULTI_ROW_STATEMENT_ROWS]
            params = [value for row in group for value in row]
            self._execute(
                build_upsert_sql(self._qualified(table), names, primary, len(group)), params
            )
            written += len(group)
        peak = max(
            record[names.index(watermark_column)]
            for record in raw
            if record[names.index(watermark_column)] is not None
        )
        self._set_watermark(table, str(_epoch_ms(peak)))
        logger.info("[aurora] %s: +%s rows (watermark %s)", table, written, peak)
        return written

    # ── serving summary ────────────────────────────────────────────────────
    def _drop_deprecated(self) -> None:
        for table in DEPRECATED_TABLES:
            if self._table_exists(table):
                self._execute(f"DROP TABLE IF EXISTS {self._qualified(table)}")
                logger.info("[aurora] %s: dropped deprecated serving table %s", self.name, table)

    def _local_tables(self) -> set[str]:
        assert self.local is not None
        rows = self.local.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _publish_summary(self) -> None:
        """Precompute the numbers the dashboard would otherwise aggregate.

        Mirrors the Turso publisher: the browser reads one row instead of
        scanning the fact tables with the read-only credential on every poll.
        """
        assert self.local is not None
        local = self.local
        existing = self._local_tables()

        def count(table: str, where: str = "") -> int:
            if table not in existing:
                return 0
            clause = f" WHERE {where}" if where else ""
            try:
                row = local.execute(f'SELECT COUNT(*) FROM "{table}"{clause}').fetchone()
            except duckdb.Error:
                return 0
            return int(row[0]) if row and row[0] is not None else 0

        def max_text(table: str, column: str) -> str | None:
            if table not in existing:
                return None
            try:
                row = local.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()
            except duckdb.Error:
                return None
            value = row[0] if row else None
            return None if value is None else str(value)

        total_flights = count("fact_flights")
        cancelled = count("fact_flights", "status = 'cancelled'")
        avg_delay = 0.0
        if "fact_flights" in existing:
            try:
                row = local.execute(
                    "SELECT AVG(delay_minutes) FROM fact_flights WHERE status != 'cancelled'"
                ).fetchone()
                avg_delay = float(row[0]) if row and row[0] is not None else 0.0
            except duckdb.Error:
                avg_delay = 0.0

        best: tuple[int, str] | None = None
        for value in (
            max_text("fact_flights", "collected_at"),
            max_text("fact_positions", "collected_at"),
            max_text("weather", "timestamp"),
        ):
            if not value:
                continue
            moment = _epoch_ms(value)
            if best is None or moment > best[0]:
                best = (moment, value)
        as_of = best[1] if best else None

        serving = [table for table in (*STATIC_TABLES, *GROWING_TABLES) if table in existing]
        catalog_counts = {table: count(table) for table in serving}
        payloads: dict[str, Any] = {
            "kpis": {
                "total_flights": total_flights,
                "avg_delay_minutes": round(avg_delay, 2),
                "cancellation_rate": (
                    round(cancelled / total_flights, 4) if total_flights else 0.0
                ),
                "airports": count("dim_airport"),
                "airlines": count("dim_airline"),
            },
            "freshness": {
                "as_of": as_of,
                "total_flights": total_flights,
                "has_flights": total_flights > 0,
            },
            "manifest": {
                "airports": count("dim_airport"),
                "flights": total_flights,
                "positions": count("fact_positions"),
            },
            "catalog": catalog_counts,
        }
        catalog_counts["site_summary"] = len(payloads)
        rows = [
            (key, json.dumps(value, separators=(",", ":"), default=str))
            for key, value in payloads.items()
        ]
        self._replace_table(
            "site_summary",
            [("key", "TEXT"), ("payload_json", "TEXT")],
            ["key", "payload_json"],
            rows,
        )
        logger.info(
            "[aurora] %s: site_summary: %s keys, %s serving tables",
            self.name,
            len(rows),
            len(serving),
        )

    def run(self) -> dict[str, int]:
        if self._connection is None and not (self.dsn or os.environ.get("PGHOST")):
            logger.warning("[aurora] %s: no DSN or PGHOST configured — skipping", self.name)
            return {}
        if not self.db_path.exists():
            logger.error("[aurora] %s: local warehouse missing: %s", self.name, self.db_path)
            return {}
        if not self._open_local():
            return {}
        if not self._connect():
            return {}
        try:
            self._ensure_state()
            self._drop_deprecated()
            for table in STATIC_TABLES:
                written = self._sync_static(table)
                if written:
                    self.counts[table] = written
            for table in GROWING_TABLES:
                written = self._sync_growing(table)
                if written:
                    self.counts[table] = written
            try:
                self._publish_summary()
            except Exception as exc:  # noqa: BLE001 - summary is best-effort
                logger.warning("[aurora] %s: site_summary publish failed: %s", self.name, exc)
            logger.info(
                "[aurora] %s: published %s tables (%s rows)",
                self.name,
                len(self.counts),
                sum(self.counts.values()),
            )
            return self.counts
        finally:
            self._close()


def publish(
    app_settings: Any = None,
    dsn: str | None = None,
    *,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Publish the serving copy to Aurora.

    ``DATABASE_URL`` is the writer the task uses; the dashboard is given the
    reader endpoint separately (``SERVING_DATABASE_URL``) and never reaches this
    path. Raises when publishing is attempted and fails.
    """
    _ = app_settings
    if not (dsn or settings.database_url or os.environ.get("PGHOST")):
        logger.warning("[aurora] no DATABASE_URL or PGHOST — skipping publish")
        return {}
    publisher = AuroraPublisher(dsn=dsn, db_path=db_path)
    return publisher.run()


def main() -> int:
    from config.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="Publish serving tables to Aurora PostgreSQL")
    parser.add_argument("--dsn", default=None, help="override DATABASE_URL")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the DSN target and exit without connecting",
    )
    args = parser.parse_args()
    if args.dry_run:
        parsed = urlparse(args.dsn or settings.database_url or "")
        host = parsed.hostname or os.environ.get("PGHOST")
        database = parsed.path.lstrip("/") or os.environ.get("PGDATABASE")
        print(f"schema={settings.pg_schema} host={host} db={database}")
        return 0
    try:
        counts = publish(dsn=args.dsn)
    except Exception as exc:  # noqa: BLE001 - report the cause, fail the step
        logger.error("[aurora] publish failed: %s", exc)
        return 1
    if not counts:
        logger.warning("[aurora] nothing published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
