"""Publish the Gold/serving tables to Turso (libSQL) for the dashboard.

Turso is the site's read layer: it supports **read-only tokens**, which the
browser can safely hold, so analytics and the SQL workbench query it directly
with no API in between. MotherDuck remains the warehouse — this is a derived,
one-way copy.

Both budgets are constraints, since Turso bills rows read and rows written:

* Small tables are fully replaced, but only when their content hash changes
  **and** a per-table refresh floor (``STATIC_REFRESH_SECONDS``) has elapsed.
  Slow-moving reference data cannot churn just because the lake runs again.
* The two growing fact tables are synced **incrementally** against a watermark
  kept in Turso's ``_sync_state`` table, so each row is written once.
* The numbers the dashboard used to aggregate on every page load (COUNT/AVG/
  MAX/GROUP BY over the facts) are precomputed locally and published as the
  one-row ``site_summary`` lookup. Reading it costs a single row per poll
  instead of scanning the whole fact table.
* Tables the site no longer needs (``fact_positions``, ``fact_notams``) are
  dropped from the serving copy: the live map reads the VPS snapshot, and the
  Gold marts carry the aggregates the pages actually render.

Free-tier Turso accounts have independent read/write budgets, so the serving
copy is spread over a fleet of databases described by ``TURSO_TARGETS``:

    TURSO_TARGETS='[
      {"name": "eu-1", "url": "libsql://...", "token": "...", "tables": ["*"]},
      {"name": "eu-2", "url": "libsql://...", "token": "...", "tables": ["*"]}
    ]'

Each target is an independent database (usually a separate Turso account). A
table named by more than one target is **mirrored**: every target keeps its own
hash/watermark state and is synced separately, so the browser can fail over to
another copy when one account is down or out of quota. ``site_summary`` is
always written to every target. A target that fails does not stop the others —
one exhausted account must not stall the whole serving copy. Tables a target is
no longer assigned are dropped from it, so changing the routing is safe and the
old copy does not linger. No Turso replication/sync is used: the targets are
plain independent copies. A target added later seeds itself incrementally: its
empty watermark means growing tables fill in at ``MAX_ROWS_PER_SYNC`` rows per
cycle while the other copies keep serving.

    python -m scripts.publish_turso
    python -m scripts.publish_turso --target eu-1          # retry one account
    python -m scripts.publish_turso --url file:/tmp/x.db   # local test
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import requests

from config.logging import logger
from config.settings import settings

# Small tables: replaced wholesale every run (cheap, always consistent).
# fact_positions and fact_notams are deliberately absent: the live map reads
# the VPS snapshot, fact_notams is only consumed through gold_notam_summary,
# and rewriting positions (up to 5k rows) every cycle dominated writes.
STATIC_TABLES = (
    "dim_airport",
    "dim_airline",
    "dim_aircraft",
    "dim_route",
    "dim_date",
    "dim_fuel",
    "fact_emissions",
    "gold_airport_metrics",
    "gold_airline_rankings",
    "gold_delay_analysis",
    "gold_weather_impact",
    "gold_seasonal_trends",
    "gold_fuel_price_series",
    "gold_aircraft_class_mix",
    "gold_airport_official_traffic",
    "gold_emissions_analysis",
    "gold_route_performance",
    "gold_sector_analysis",
    "gold_notam_summary",
    "gold_aircraft_utilization",
    "site_stories",
    "site_ops",
    "site_lineage",
)
# Tables that older versions of this publisher put in Turso and nothing reads
# any more. They are dropped once so the catalog reflects what actually serves.
DEPRECATED_TABLES = ("fact_positions", "fact_notams")
# Growing tables: upserted incrementally. table → (primary key, watermark column)
GROWING_TABLES: dict[str, tuple[tuple[str, ...], str]] = {
    # collected_at, not actual_arrival: en-route and cancelled flights have no
    # arrival, and arrivals are backfilled with older timestamps.
    "fact_flights": (("flight_id",), "collected_at"),
    "weather": (("station_icao", "timestamp"), "timestamp"),
}
BATCH_SIZE = 400
# Rows per INSERT statement (400 separate statements were the bottleneck).
MULTI_ROW_STATEMENT_ROWS = 100
MAX_ROWS_PER_SYNC = 20_000
# Static tables upload only when their content hash changes, and a table with
# an entry here also waits out that many seconds between uploads. Without the
# floor a rebuilt reference table (or a large derived mart) would be written in
# full on every lake cycle because the warehouse replaces its source files.
# Floors apply to large/slow tables only; small marts update every cycle.
STATIC_REFRESH_SECONDS: dict[str, int] = {
    # Reference dimensions: daily is generous, they change on upstream updates.
    "dim_airport": 86_400,
    "dim_route": 86_400,
    "dim_fuel": 86_400,
    "dim_date": 86_400,
    # Large derived series: hours, not minutes, are the useful resolution.
    "gold_fuel_price_series": 21_600,
    "gold_route_performance": 3_600,
    "gold_sector_analysis": 3_600,
    # Eurostat publishes monthly with a two-month lag; daily is ample.
    "gold_airport_official_traffic": 86_400,
}
# When a growing table is recreated after a schema change it must be re-seeded;
# the old watermark points past rows that no longer exist.
GROWING_RESET_LOOKBACK_MS: dict[str, int] = {
    # Keep re-seeds bounded: Turso's HTTP write path is slow (hundreds of rows
    # per second), so a week of flights is a multi-minute step.
    "fact_flights": 24 * 3_600_000,
    "weather": 12 * 3_600_000,
}
# Every table this publisher may manage in a target. Used to validate routing
# config and to decide what an unassigned target should drop.
SERVING_TABLES = frozenset((*STATIC_TABLES, *GROWING_TABLES, "site_summary"))
# Name of the implicit target built from the legacy single-database variables.
DEFAULT_TARGET_NAME = "primary"


@dataclass(frozen=True)
class TursoTarget:
    """One independent Turso database in the serving fleet.

    ``tables`` is the set of serving tables this database is responsible for;
    an empty tuple means every table. Naming a table in several targets mirrors
    it: each copy is synced and versioned independently, so a browser can fail
    over to another when this account is down or out of free-tier quota.
    ``site_summary`` is implicit and always written everywhere.
    """

    name: str
    url: str
    token: str | None
    tables: tuple[str, ...] = ()

    def owns(self, table: str) -> bool:
        return not self.tables or table in self.tables


def parse_targets(
    raw: str | None,
    *,
    url: str | None = None,
    token: str | None = None,
) -> list[TursoTarget]:
    """Build the target fleet from ``TURSO_TARGETS`` or the legacy URL vars.

    An explicit ``url`` (CLI override, tests) always means one target owning
    everything. Malformed JSON, duplicate names and unknown table names raise:
    a routing typo must fail the run, not silently publish to the wrong place.
    """
    if url:
        return [TursoTarget(DEFAULT_TARGET_NAME, url, token)]

    raw = (raw or "").strip()
    if not raw:
        if settings.turso_database_url:
            return [
                TursoTarget(
                    DEFAULT_TARGET_NAME, settings.turso_database_url, settings.turso_auth_token
                )
            ]
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"[turso] TURSO_TARGETS is not valid JSON: {exc}. Each entry must look like "
            '{"name": "eu-1", "url": "libsql://...", "token": "..."}'
        ) from exc
    if not isinstance(data, list) or not data:
        raise RuntimeError("[turso] TURSO_TARGETS must be a non-empty JSON array")

    targets: list[TursoTarget] = []
    seen: set[str] = set()
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise RuntimeError(f"[turso] TURSO_TARGETS[{index}] must be an object")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise RuntimeError(f"[turso] TURSO_TARGETS[{index}] needs a name")
        if name in seen:
            raise RuntimeError(f"[turso] duplicate TURSO_TARGETS name: {name}")
        seen.add(name)

        db_url = str(entry.get("url") or "").strip()
        if not db_url:
            raise RuntimeError(f"[turso] TURSO_TARGETS target {name} needs a url")

        tables_raw = entry.get("tables")
        if tables_raw in (None, "", "*"):
            tables: tuple[str, ...] = ()
        elif isinstance(tables_raw, str):
            tables = tuple(t.strip() for t in tables_raw.split(",") if t.strip())
        elif isinstance(tables_raw, list):
            listed = [str(t).strip() for t in tables_raw if str(t).strip()]
            # A "*" anywhere in the list means the same as listing none.
            tables = () if "*" in listed else tuple(listed)
        else:
            raise RuntimeError(f"[turso] TURSO_TARGETS target {name}: tables must be a list")
        unknown = [t for t in tables if t not in SERVING_TABLES]
        if unknown:
            raise RuntimeError(
                f"[turso] TURSO_TARGETS target {name} names unknown tables: " + ", ".join(unknown)
            )

        token_value = entry.get("token")
        targets.append(
            TursoTarget(
                name=name,
                url=db_url,
                token=str(token_value) if token_value else None,
                tables=tables,
            )
        )
    return targets


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


def _epoch_ms(value: Any) -> int:
    """Milliseconds since epoch for a datetime, date or ISO-8601 string.

    ``collected_at`` reaches DuckDB as a VARCHAR (Silver keeps the ISO text),
    while ``timestamp`` is a real TIMESTAMPTZ; the watermark must accept both.
    """
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return int(moment.timestamp() * 1000)
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


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
        self,
        url: str | None = None,
        token: str | None = None,
        db_path: Path | None = None,
        *,
        name: str = DEFAULT_TARGET_NAME,
        tables: tuple[str, ...] = (),
        materialize_site: bool = True,
    ):
        self.name = name
        self.url = url or settings.turso_database_url
        self.token = token or settings.turso_auth_token
        self.db_path = Path(db_path or settings.duckdb_path)
        # Empty means every serving table; otherwise this target owns only the
        # listed tables (mirrors are just the same table in several targets).
        self.tables = tables
        self.materialize_site = materialize_site
        self.local: duckdb.DuckDBPyConnection | None = None
        self.remote: Any = None
        self.counts: dict[str, int] = {}

    def _owns(self, table: str) -> bool:
        return not self.tables or table in self.tables

    # ── connections ────────────────────────────────────────────────────────
    def _connect(self) -> bool:
        if not self.url:
            logger.warning("[turso] %s: no URL configured — skipping", self.name)
            return False
        if not self.db_path.exists():
            logger.error("[turso] %s: local warehouse missing: %s", self.name, self.db_path)
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

    def _table_exists(self, table: str) -> bool:
        """Authoritative existence check, used by every before/after decision.

        The swap used to infer existence from ``pragma_table_info`` with a
        broad ``except`` that read any transport hiccup as "does not exist". The
        outgoing table was then never moved aside and the final rename hit the
        name that was still occupied (Turso: "already another table or index
        with this name"). sqlite_master cannot fail that way, and a transport
        error here raises instead of silently answering no.
        """
        result = self.remote.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1", [table]
        )
        return bool(result.rows)

    def _sqlite_columns(self, table: str) -> list[str]:
        """Column names for an existing table, or [] when it has no columns yet."""
        result = self.remote.execute("SELECT name FROM pragma_table_info(?) ORDER BY cid", [table])
        return [str(r[0]) for r in result.rows]

    def _ensure_table(self, table: str, columns: list[tuple[str, str]], replace: bool) -> bool:
        """Returns True only when an existing table was recreated (schema change).

        A brand-new table must keep the caller's initial watermark; recreating
        one with data in it invalidates the stored watermark.
        """
        wanted = [c for c, _ in columns]
        if not self._table_exists(table):
            self._create_table(table, columns)
            return False
        existing = self._sqlite_columns(table)
        if replace or existing != wanted:
            self.remote.execute(f'DROP TABLE IF EXISTS "{table}"')
            self._create_table(table, columns)
            return True
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
        column_list = ", ".join(f'"{c}"' for c in columns)
        row_placeholder = "(" + ", ".join("?" for _ in columns) + ")"
        written = 0
        for start in range(0, len(rows), BATCH_SIZE):
            chunk = rows[start : start + BATCH_SIZE]
            # One multi-row INSERT per statement instead of one per row: Turso
            # parses far fewer statements, which is what made bulk syncs slow.
            # Statements stay modest (request-size safety) and still travel as
            # one pipeline request per chunk.
            statements = []
            for index in range(0, len(chunk), MULTI_ROW_STATEMENT_ROWS):
                group = chunk[index : index + MULTI_ROW_STATEMENT_ROWS]
                values = ", ".join(row_placeholder for _ in group)
                statements.append(
                    (
                        f'INSERT OR REPLACE INTO "{table}" ({column_list}) VALUES {values}',
                        [value for row in group for value in row],
                    )
                )
            self.remote.batch(statements)  # type: ignore[union-attr]
            written += len(chunk)
        return written

    def _replace_table(
        self,
        table: str,
        columns: list[tuple[str, str]],
        names: list[str],
        rows: list[tuple[Any, ...]],
    ) -> None:
        """Load a shadow table and swap it in, so readers never see it empty.

        The swap is sent as one pipeline request, which Turso executes as a
        single transaction. That closes the window where a crash between the two
        renames would leave the table missing, and it means a failure leaves the
        outgoing table exactly where it was.
        """
        shadow, previous = f"{table}__new", f"{table}__old"
        defs = ", ".join(f'"{c}" {_sqlite_type(t)}' for c, t in columns)
        self.remote.execute(f'DROP TABLE IF EXISTS "{shadow}"')
        self.remote.execute(f'CREATE TABLE "{shadow}" ({defs})')
        self._write_rows(shadow, names, rows)
        swap: list[tuple[str, Any]] = [(f'DROP TABLE IF EXISTS "{previous}"', None)]
        if self._table_exists(table):
            swap.append((f'ALTER TABLE "{table}" RENAME TO "{previous}"', None))
        swap.append((f'ALTER TABLE "{shadow}" RENAME TO "{table}"', None))
        swap.append((f'DROP TABLE IF EXISTS "{previous}"', None))
        try:
            self.remote.batch(swap)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            # sqlite names are global, so anything at all sitting under this
            # name blocks the rename. The shadow holds a complete copy of the
            # rows, so clearing the name first cannot lose data, and the object
            # types are logged in case something unexpected turns up.
            logger.warning(
                "[turso] swap of %s hit %s; clearing the name and retrying",
                table,
                exc,
            )
            logger.warning("[turso] objects named %r: %s", table, self._objects_named(table))
            self._clear_name(table)
            self._clear_name(previous)
            self.remote.batch(  # type: ignore[union-attr]
                [(f'ALTER TABLE "{shadow}" RENAME TO "{table}"', None)]
            )

    def _objects_named(self, name: str) -> list[tuple[str, str]]:
        """Everything in sqlite_master under this exact name (table or index)."""
        rows = self.remote.execute(
            "SELECT type, name FROM sqlite_master WHERE name = ?", [name]
        ).rows
        return [(str(row[0]), str(row[1])) for row in rows]

    def _clear_name(self, name: str) -> None:
        """Drop whatever occupies ``name``, whether it is a table or an index."""
        for kind, object_name in self._objects_named(name):
            if kind == "index":
                self.remote.execute(f'DROP INDEX IF EXISTS "{object_name}"')
            else:
                self.remote.execute(f'DROP TABLE IF EXISTS "{object_name}"')

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
            # Changed, but this table has a refresh floor: re-publishing a
            # large reference/mart table every cycle wastes the write budget.
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
        self._set_watermark(table, str(_epoch_ms(peak)))
        logger.info("[turso] %s: +%s rows (watermark %s)", table, written, peak)
        return written

    # ── serving summary ────────────────────────────────────────────────────
    def _drop_deprecated(self) -> None:
        """Remove serving tables the dashboard no longer reads (once)."""
        for table in DEPRECATED_TABLES:
            if self._table_exists(table):
                self.remote.execute(f'DROP TABLE IF EXISTS "{table}"')
                logger.info("[turso] %s: dropped deprecated serving table %s", self.name, table)

    def _drop_orphans(self) -> None:
        """Drop serving tables (and stranded swap shadows) this target lost.

        Routing changes: a table moves to another account, a mirror is removed.
        Without this cleanup the old copy keeps occupying storage and hides the
        routing drift. Only tables this publisher knows are ever touched.
        """
        assigned = set(self.tables) if self.tables else set(SERVING_TABLES)
        assigned.add("site_summary")
        present = {
            str(row[0])
            for row in self.remote.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).rows
        }
        for table in sorted(SERVING_TABLES):
            if table in assigned:
                # An interrupted shadow swap can strand a copy; clear it.
                for suffix in ("__new", "__old"):
                    shadow = f"{table}{suffix}"
                    if shadow in present:
                        self.remote.execute(f'DROP TABLE IF EXISTS "{shadow}"')
                        logger.info("[turso] %s: dropped stranded %s", self.name, shadow)
                continue
            if table in present:
                self.remote.execute(f'DROP TABLE IF EXISTS "{table}"')
                logger.info("[turso] %s: dropped unassigned serving table %s", self.name, table)

    def _local_tables(self) -> set[str]:
        """Names of the local warehouse tables/views in the main schema."""
        assert self.local is not None
        rows = self.local.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _publish_summary(self) -> None:
        """Publish the numbers the dashboard used to aggregate in Turso.

        Every page poll used to COUNT/AVG/MAX the fact tables with the browser's
        read-only token: each of those queries scans the whole table, so reads
        scaled with table size × refreshes × visitors. The same numbers are
        computed here from the local warehouse (free) and served as a handful of
        one-row payloads, so a browser poll reads one row instead of millions.

        Keys: ``kpis``, ``freshness``, ``manifest`` (counts) and ``catalog``
        (row count per serving table). Best-effort: the site falls back to the
        direct aggregate queries when this table is missing.
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
                # Schema drift in the local warehouse must not sink the summary.
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
                avg_row = local.execute(
                    "SELECT AVG(delay_minutes) FROM fact_flights WHERE status != 'cancelled'"
                ).fetchone()
                avg_delay = float(avg_row[0]) if avg_row and avg_row[0] is not None else 0.0
            except duckdb.Error:
                avg_delay = 0.0

        # "Data as of" is the newest of the same three candidates the client
        # used to MAX() directly; compare on epoch so mixed text/timestamp
        # representations cannot sort wrongly.
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
                # The live map reads positions from the VPS snapshot; this is
                # the warehouse inventory the header shows, not a serving table.
                "positions": count("fact_positions"),
            },
            "catalog": catalog_counts,
        }
        # The summary is itself a serving table (one row per payload key).
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
            "[turso] %s: site_summary: %s keys, %s serving tables",
            self.name,
            len(rows),
            len(serving),
        )

    def run(self) -> dict[str, int]:
        if not self.url:
            import os

            if os.environ.get("TURSO_DATABASE_URL", "unset") == "":
                raise RuntimeError("[turso] TURSO_DATABASE_URL is set but empty — fix the secret")
            logger.warning("[turso] %s: no URL configured — skipping", self.name)
            return {}
        if not self.db_path.exists():
            logger.error("[turso] %s: local warehouse missing: %s", self.name, self.db_path)
            return {}

        # Site payloads are computed, not stored: materialise them locally first
        # (this needs a write connection, before the read-only one opens). When
        # a run has several targets, publish() does this once up front.
        if self.materialize_site:
            from scripts.publish_site_tables import build_site_payloads, write_site_tables_local

            try:
                write_site_tables_local(build_site_payloads(), db_path=self.db_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[turso] %s: site payloads unavailable: %s", self.name, exc)

        if not self._connect():
            return {}
        try:
            self.remote.execute(
                "CREATE TABLE IF NOT EXISTS _sync_state "
                "(table_name TEXT PRIMARY KEY, watermark TEXT)"
            )
            self._drop_deprecated()
            self._drop_orphans()
            for table in STATIC_TABLES:
                if not self._owns(table):
                    continue
                written = self._sync_static(table)
                if written:
                    self.counts[table] = written
            for table in GROWING_TABLES:
                if not self._owns(table):
                    continue
                written = self._sync_growing(table)
                if written:
                    self.counts[table] = written
            # Last, so counts and freshness reflect everything just written.
            # Always published, even to a target that owns nothing else: every
            # copy must be able to serve the summary when a peer fails over.
            # Best-effort: the site falls back to direct aggregate reads until
            # the next successful publish writes it.
            try:
                self._publish_summary()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[turso] %s: site_summary publish failed: %s", self.name, exc)

            logger.info(
                "[turso] %s: published %s tables → %s (rows: %s)",
                self.name,
                len(self.counts),
                (self.url or "").split("?")[0],
                sum(self.counts.values()),
            )
            return self.counts
        finally:
            self._close()


def publish(
    app_settings: Any = None,
    url: str | None = None,
    token: str | None = None,
    *,
    targets_raw: str | None = None,
    only: Iterable[str] | None = None,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Publish every configured target, isolating failures per target.

    A down or quota-exhausted account must not stop the others: each target is
    synced independently and a failure is logged while its mirrors keep serving
    the site. Only when every selected target fails does this raise.
    """
    _ = app_settings
    targets = parse_targets(
        settings.turso_targets if targets_raw is None else targets_raw,
        url=url,
        token=token,
    )
    if only is not None:
        wanted = set(only)
        known = {target.name for target in targets}
        unknown = wanted - known
        if unknown:
            raise RuntimeError("[turso] unknown target(s): " + ", ".join(sorted(unknown)))
        targets = [target for target in targets if target.name in wanted]
    if not targets:
        logger.warning("[turso] no targets configured — skipping")
        return {}

    path = Path(db_path or settings.duckdb_path)
    if not path.exists():
        logger.error("[turso] local warehouse missing: %s", path)
        return {}

    # Site payloads are computed, not stored: materialise them once for every
    # target, which then syncs its own copy from the local tables.
    try:
        from scripts.publish_site_tables import build_site_payloads, write_site_tables_local

        write_site_tables_local(build_site_payloads(), db_path=path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[turso] site payloads unavailable: %s", exc)

    combined: dict[str, int] = {}
    failures: list[str] = []
    for target in targets:
        publisher = TursoPublisher(
            url=target.url,
            token=target.token,
            db_path=path,
            name=target.name,
            tables=target.tables,
            materialize_site=False,
        )
        try:
            counts = publisher.run()
        except Exception as exc:  # noqa: BLE001 - the other targets must proceed
            failures.append(target.name)
            reason = str(exc)
            if "writes are blocked" in reason or "Operation was blocked" in reason:
                # Free-tier write budget exhausted (or the database is read-only
                # for another reason). Reads still work, so its mirrors serve;
                # publishing to it resumes by itself when the quota resets.
                logger.warning(
                    "[turso] %s: writes are blocked (free-plan write budget exhausted?) — "
                    "its mirrors keep serving until the quota resets",
                    target.name,
                )
            else:
                logger.error("[turso] %s: publish failed: %s", target.name, exc)
            continue
        for table, written in counts.items():
            combined[table] = combined.get(table, 0) + written

    if failures and len(failures) == len(targets):
        raise RuntimeError("[turso] every target failed: " + ", ".join(failures))
    if failures:
        logger.warning(
            "[turso] %s of %s targets failed (%s); mirrors cover the rest",
            len(failures),
            len(targets),
            ", ".join(failures),
        )
    return combined


def main() -> int:
    from config.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="Publish serving tables to Turso")
    parser.add_argument(
        "--url",
        default=None,
        help="publish a single database, ignoring TURSO_TARGETS (file: works)",
    )
    parser.add_argument("--token", default=None, help="override TURSO_AUTH_TOKEN")
    parser.add_argument(
        "--target",
        action="append",
        default=None,
        metavar="NAME",
        help="sync only this target (repeatable); default: every target",
    )
    parser.add_argument(
        "--targets", default=None, help="override TURSO_TARGETS with this JSON array (testing)"
    )
    args = parser.parse_args()
    try:
        counts = publish(url=args.url, token=args.token, targets_raw=args.targets, only=args.target)
    except Exception as exc:  # noqa: BLE001 - report the cause, fail the step
        logger.error("[turso] publish failed: %s", exc)
        return 1
    if not counts:
        logger.warning("[turso] nothing published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
