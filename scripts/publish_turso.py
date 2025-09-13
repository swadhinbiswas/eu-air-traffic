"""Publish the Gold/serving tables to Turso (libSQL) for the dashboard.

Turso is the site's read layer: it supports **read-only tokens**, which the
browser can safely hold, so analytics and the SQL workbench query it directly
with no API in between. MotherDuck remains the warehouse — this is a derived,
one-way copy.

Write budget is the constraint: the free tier allows a limited number of row
writes per month, so small tables are fully replaced while the two growing
fact tables are synced **incrementally** against a watermark kept in Turso's
``_sync_state`` table.

    python -m scripts.publish_turso
    python -m scripts.publish_turso --url file:/tmp/x.db   # local test
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import requests

from config.logging import logger
from config.settings import settings

# Small tables: replaced wholesale every run (cheap, always consistent).
STATIC_TABLES = (
    "dim_airport",
    "dim_airline",
    "dim_aircraft",
    "dim_route",
    "dim_date",
    "dim_fuel",
    "fact_emissions",
    "fact_notams",
    "fact_positions",
    "gold_airport_metrics",
    "gold_airline_rankings",
    "gold_delay_analysis",
    "gold_weather_impact",
    "gold_seasonal_trends",
    "gold_fuel_price_series",
    "gold_aircraft_class_mix",
    "gold_airport_official_traffic",
    "site_stories",
    "site_ops",
    "site_lineage",
)
# Growing tables: upserted incrementally. table → (primary key, watermark column)
GROWING_TABLES: dict[str, tuple[tuple[str, ...], str]] = {
    # collected_at, not actual_arrival: en-route and cancelled flights have no
    # arrival, and arrivals are backfilled with older timestamps.
    "fact_flights": (("flight_id",), "collected_at"),
    "weather": (("station_icao", "timestamp"), "timestamp"),
}
BATCH_SIZE = 400
MAX_ROWS_PER_SYNC = 250_000
# Static tables upload only when their content hash changes. Positions move
# every cycle, so their refresh is capped to protect the free write budget.
STATIC_REFRESH_SECONDS: dict[str, int] = {"fact_positions": 900}
# When a growing table is recreated after a schema change it must be re-seeded;
# the old watermark points past rows that no longer exist.
GROWING_RESET_LOOKBACK_MS: dict[str, int] = {
    "fact_flights": 7 * 24 * 3_600_000,
    "weather": 24 * 3_600_000,
}


def _sqlite_type(duck_type: str) -> str:
    t = (duck_type or "").upper()
    if t.startswith(("BOOL",)):
        return "INTEGER"
    if any(x in t for x in ("INT",)):
        return "INTEGER"
    if any(x in t for x in ("DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL")):
        return "REAL"
    return "TEXT"


def _value(value: Any) -> Any:
    """Convert a DuckDB value into something libSQL accepts."""
    if value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return json.dumps(value, default=str)


class _Rows:
    """Result wrapper exposing the ``.rows`` attribute the publisher uses."""

    def __init__(self, rows: list[list[Any]]) -> None:
        self.rows = rows


class _TursoHttpClient:
    """Minimal client for Turso's HTTP pipeline API (``POST /v2/pipeline``).

    The ``libsql-client`` Python package is unmaintained and speaks the retired
    Hrana v1 protocol (``wss://`` + ``/v1/execute``), which Turso rejects with
    HTTP 400. The v2 pipeline is the same protocol ``@libsql/client/web`` uses
    in the browser, so the write path and the read path agree.
    """

    def __init__(self, url: str, token: str, timeout: float = 60.0) -> None:
        base = url
        for scheme in ("libsql://", "wss://"):
            if base.startswith(scheme):
                base = "https://" + base[len(scheme) :]
        self._endpoint = base.rstrip("/") + "/v2/pipeline"
        self._timeout = timeout
        self._session = requests.Session()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._session.headers.update(headers)

    @staticmethod
    def _arg(value: Any) -> dict[str, Any]:
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "integer", "value": "1" if value else "0"}
        if isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        if isinstance(value, float):
            # Hrana encodes integers as strings (to dodge JSON precision loss)
            # but floats must be JSON numbers, and JSON cannot hold NaN/Inf.
            if math.isnan(value) or math.isinf(value):
                return {"type": "null"}
            return {"type": "float", "value": value}
        if isinstance(value, (bytes, bytearray)):
            return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
        return {"type": "text", "value": str(value)}

    @staticmethod
    def _cell(cell: dict[str, Any]) -> Any:
        kind = cell.get("type")
        if kind == "null":
            return None
        if kind == "integer":
            return int(cell["value"])
        if kind == "float":
            return float(cell["value"])
        if kind == "blob":
            return base64.b64decode(cell.get("base64") or "")
        return cell.get("value")

    @staticmethod
    def _statement(sql: str, args: Any) -> dict[str, Any]:
        return {
            "type": "execute",
            "stmt": {
                "sql": sql,
                "args": [_TursoHttpClient._arg(value) for value in (args or [])],
            },
        }

    def _pipeline(self, payload: list[dict[str, Any]]) -> list[list[Any]]:
        response = self._session.post(
            self._endpoint,
            json={"requests": payload},
            timeout=self._timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Turso HTTP {response.status_code}: {response.text[:300]}")
        results: list[list[Any]] = []
        for entry in response.json().get("results", []):
            if entry.get("type") != "ok":
                message = (entry.get("error") or {}).get("message", "unknown error")
                raise RuntimeError(f"Turso error: {message}")
            body = entry.get("response") or {}
            if body.get("type") != "execute":
                results.append([])
                continue
            result = body.get("result") or {}
            results.append([[self._cell(cell) for cell in row] for row in result.get("rows", [])])
        return results

    def execute(self, sql: str, args: Any = None) -> _Rows:
        rows = self._pipeline([self._statement(sql, args)])
        return _Rows(rows[0] if rows else [])

    def batch(self, statements: list[tuple[str, Any]]) -> None:
        payload = [self._statement(sql, args) for sql, args in statements]
        if payload:
            self._pipeline(payload)

    def close(self) -> None:
        self._session.close()


class TursoPublisher:
    def __init__(
        self, url: str | None = None, token: str | None = None, db_path: Path | None = None
    ):
        self.url = url or settings.turso_database_url
        self.token = token or settings.turso_auth_token
        self.db_path = Path(db_path or settings.duckdb_path)
        self.local: duckdb.DuckDBPyConnection | None = None
        self.remote: Any = None
        self.counts: dict[str, int] = {}

    # ── connections ────────────────────────────────────────────────────────
    def _connect(self) -> bool:
        if not self.url:
            logger.warning("[turso] TURSO_DATABASE_URL not set — skipping")
            return False
        if not self.db_path.exists():
            logger.error("[turso] local warehouse missing: %s", self.db_path)
            return False
        self.local = duckdb.connect(str(self.db_path), read_only=True)
        if self.url.startswith(("file:", "sqlite:")):
            # Local libSQL file, used by the tests.
            from libsql_client import create_client_sync

            kwargs: dict[str, Any] = {"url": self.url}
            if self.token:
                kwargs["auth_token"] = self.token
            self.remote = create_client_sync(**kwargs)
        else:
            self.remote = _TursoHttpClient(self.url, self.token or "")
        return True

    def _close(self) -> None:
        if self.local is not None:
            self.local.close()
            self.local = None
        if self.remote is not None:
            try:
                self.remote.close()
            except Exception:  # noqa: BLE001
                pass
            self.remote = None

    # ── schema ─────────────────────────────────────────────────────────────
    def _columns(self, table: str) -> list[tuple[str, str]]:
        assert self.local is not None
        try:
            rows = self.local.execute(f'DESCRIBE "{table}"').fetchall()
        except duckdb.Error:
            return []
        return [(str(r[0]), str(r[1])) for r in rows]

    def _sqlite_columns(self, table: str) -> list[str]:
        try:
            result = self.remote.execute(
                "SELECT name FROM pragma_table_info(?) ORDER BY cid", [table]
            )
            return [str(r[0]) for r in result.rows]
        except Exception:  # noqa: BLE001 - table does not exist yet
            return []

    def _ensure_table(self, table: str, columns: list[tuple[str, str]], replace: bool) -> bool:
        """Returns True only when an existing table was recreated (schema change).

        A brand-new table must keep the caller's initial watermark; recreating
        one with data in it invalidates the stored watermark.
        """
        wanted = [c for c, _ in columns]
        existing = self._sqlite_columns(table)
        if existing and (replace or existing != wanted):
            self.remote.execute(f'DROP TABLE IF EXISTS "{table}"')
            self._create_table(table, columns)
            return True
        if existing:
            return False
        self._create_table(table, columns)
        return False

    def _create_table(self, table: str, columns: list[tuple[str, str]]) -> None:
        defs = ", ".join(f'"{c}" {_sqlite_type(t)}' for c, t in columns)
        primary = GROWING_TABLES.get(table, ((), ""))[0]
        if primary:
            defs += ", PRIMARY KEY (" + ", ".join(f'"{c}"' for c in primary) + ")"
        self.remote.execute(f'CREATE TABLE "{table}" ({defs})')

    # ── state ──────────────────────────────────────────────────────────────
    def _state_get(self, table: str) -> str:
        try:
            rows = self.remote.execute(
                "SELECT watermark FROM _sync_state WHERE table_name = ?", [table]
            ).rows
            return str(rows[0][0]) if rows else ""
        except Exception:  # noqa: BLE001 - created lazily by the caller
            return ""

    def _state_set(self, table: str, value: str) -> None:
        self.remote.execute(
            "INSERT OR REPLACE INTO _sync_state (table_name, watermark) VALUES (?, ?)",
            [table, value],
        )

    def _watermark(self, table: str) -> str:
        value = self._state_get(table)
        if value.startswith("wm:"):
            return value[3:]
        # Legacy rows stored a bare epoch; anything else is unusable.
        return value if value.isdigit() else "0"

    def _set_watermark(self, table: str, watermark: str) -> None:
        self._state_set(table, f"wm:{watermark}")

    def _static_state(self, table: str) -> tuple[str, int]:
        """Return (content hash, last upload epoch ms) for a static table."""
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
        placeholders = ", ".join("?" for _ in columns)
        column_list = ", ".join(f'"{c}"' for c in columns)
        sql = f'INSERT OR REPLACE INTO "{table}" ({column_list}) VALUES ({placeholders})'
        written = 0
        for start in range(0, len(rows), BATCH_SIZE):
            chunk = rows[start : start + BATCH_SIZE]
            self.remote.batch([(sql, list(row)) for row in chunk])  # type: ignore[union-attr]
            written += len(chunk)
        return written

    def _replace_table(
        self,
        table: str,
        columns: list[tuple[str, str]],
        names: list[str],
        rows: list[tuple[Any, ...]],
    ) -> None:
        """Load a shadow table and swap it in, so readers never see it empty."""
        shadow, previous = f"{table}__new", f"{table}__old"
        defs = ", ".join(f'"{c}" {_sqlite_type(t)}' for c, t in columns)
        self.remote.execute(f'DROP TABLE IF EXISTS "{shadow}"')
        self.remote.execute(f'CREATE TABLE "{shadow}" ({defs})')
        self._write_rows(shadow, names, rows)
        had_table = bool(self._sqlite_columns(table))
        self.remote.execute(f'DROP TABLE IF EXISTS "{previous}"')
        if had_table:
            self.remote.execute(f'ALTER TABLE "{table}" RENAME TO "{previous}"')
        self.remote.execute(f'ALTER TABLE "{shadow}" RENAME TO "{table}"')
        self.remote.execute(f'DROP TABLE IF EXISTS "{previous}"')

    def _sync_static(self, table: str) -> int:
        columns = self._columns(table)
        if not columns:
            logger.warning("[turso] local table %s missing — skipped", table)
            return 0
        names = [c for c, _ in columns]
        select = ", ".join(f'"{c}"' for c in names)
        raw = self.local.execute(f'SELECT {select} FROM "{table}"').fetchall()  # type: ignore[union-attr]
        rows = [tuple(_value(v) for v in record) for record in raw]
        digest = hashlib.sha256(
            json.dumps(rows, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        previous_digest, last_sync_ms = self._static_state(table)
        if previous_digest == digest:
            # Identical content: no delete, no insert, no write cost.
            return 0
        now_ms = int(time.time() * 1000)
        refresh_ms = STATIC_REFRESH_SECONDS.get(table, 0) * 1000
        if refresh_ms and now_ms - last_sync_ms < refresh_ms:
            # Changed, but this table has a refresh floor (positions move every
            # cycle; re-publishing them every run wastes the write budget).
            return 0
        self._replace_table(table, columns, names, rows)
        self._state_set(table, f"static:{now_ms}:{digest}")
        return len(rows)

    def _sync_growing(self, table: str) -> int:
        primary, watermark_column = GROWING_TABLES[table]
        _ = primary
        columns = self._columns(table)
        if not columns:
            logger.warning("[turso] local table %s missing — skipped", table)
            return 0
        recreated = self._ensure_table(table, columns, replace=False)
        if recreated:
            # The old watermark points past rows that no longer exist; re-seed
            # a recent window instead of leaving the serving copy empty.
            lookback = GROWING_RESET_LOOKBACK_MS.get(table, 24 * 3_600_000)
            self._set_watermark(table, str(int(time.time() * 1000) - lookback))
            logger.warning("[turso] %s was recreated — re-seeding recent rows", table)
        names = [c for c, _ in columns]
        select = ", ".join(f'"{c}"' for c in names)

        # Watermarks are epoch milliseconds: absolute, so no timezone or
        # string-format comparison pitfalls. Strict ">" — a correction to an
        # existing flight is re-sent under a newer collected_at, so the same
        # flight_id simply upserts.
        watermark = self._watermark(table) or "0"
        raw = self.local.execute(  # type: ignore[union-attr]
            f'SELECT {select} FROM "{table}" '
            f'WHERE "{watermark_column}" IS NOT NULL '
            f'AND epoch_ms(CAST("{watermark_column}" AS TIMESTAMPTZ)) > CAST(? AS BIGINT) '
            f'ORDER BY "{watermark_column}" LIMIT {MAX_ROWS_PER_SYNC}',
            [watermark],
        ).fetchall()
        if not raw:
            return 0

        rows = [tuple(_value(v) for v in record) for record in raw]
        written = self._write_rows(table, names, rows)

        peak = max(
            record[names.index(watermark_column)]
            for record in raw
            if record[names.index(watermark_column)] is not None
        )
        self._set_watermark(table, str(int(peak.timestamp() * 1000)))
        logger.info("[turso] %s: +%s rows (watermark %s)", table, written, peak)
        return written

    def run(self) -> dict[str, int]:
        from scripts.publish_site_tables import build_site_payloads, write_site_tables_local

        if not self.url:
            import os

            if os.environ.get("TURSO_DATABASE_URL", "unset") == "":
                raise RuntimeError("[turso] TURSO_DATABASE_URL is set but empty — fix the secret")
            logger.warning("[turso] TURSO_DATABASE_URL not set — skipping")
            return {}
        if not self.db_path.exists():
            logger.error("[turso] local warehouse missing: %s", self.db_path)
            return {}

        # Site payloads are computed, not stored: materialise them locally first
        # (this needs a write connection, before the read-only one opens).
        try:
            write_site_tables_local(build_site_payloads(), db_path=self.db_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[turso] site payloads unavailable: %s", exc)

        if not self._connect():
            return {}
        try:
            self.remote.execute(
                "CREATE TABLE IF NOT EXISTS _sync_state "
                "(table_name TEXT PRIMARY KEY, watermark TEXT)"
            )
            for table in STATIC_TABLES:
                written = self._sync_static(table)
                if written:
                    self.counts[table] = written
            for table in GROWING_TABLES:
                written = self._sync_growing(table)
                if written:
                    self.counts[table] = written

            logger.info(
                "[turso] published %s tables → %s (rows: %s)",
                len(self.counts),
                (self.url or "").split("?")[0],
                sum(self.counts.values()),
            )
            return self.counts
        finally:
            self._close()


def publish(
    app_settings: Any = None, url: str | None = None, token: str | None = None
) -> dict[str, int]:
    _ = app_settings
    return TursoPublisher(url=url, token=token).run()


def main() -> int:
    from config.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="Publish serving tables to Turso")
    parser.add_argument("--url", default=None, help="override TURSO_DATABASE_URL (file: works)")
    parser.add_argument("--token", default=None, help="override TURSO_AUTH_TOKEN")
    args = parser.parse_args()
    try:
        counts = publish(url=args.url, token=args.token)
    except Exception as exc:  # noqa: BLE001 - never fail the whole workflow
        logger.error("[turso] publish failed: %s", exc)
        return 1
    if not counts:
        logger.warning("[turso] nothing published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
