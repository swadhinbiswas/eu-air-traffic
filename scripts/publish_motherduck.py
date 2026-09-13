"""Publish the built DuckDB warehouse to MotherDuck (the Gold serving layer).

The GitHub Actions warehouse job builds the star schema and dbt marts locally
(reproducible, fast) and then copies every schema/table to MotherDuck, where the
dashboard's SQL page, analytics and BI tools read them.

    python -m scripts.publish_motherduck

Best-effort: exits 0 with a warning when ``MOTHERDUCK_TOKEN`` is unset so the
job still produces the static bundle.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb

from config.logging import logger
from config.settings import settings

_LOCAL_SCHEMAS = ("main", "staging", "marts", "reports")


def _local_objects(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    rows = con.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('main', 'staging', 'marts', 'reports')
        ORDER BY table_schema, table_name
        """
    ).fetchall()
    return [(str(schema), str(table)) for schema, table in rows]


def publish(local_path: Path | None = None) -> int:
    """Copy every local schema/table to MotherDuck. Returns tables published."""
    s = settings
    if not s.motherduck_enabled:
        if os.environ.get("MOTHERDUCK_TOKEN", "unset") == "":
            raise RuntimeError("[motherduck] MOTHERDUCK_TOKEN is set but empty — fix the secret")
        logger.warning("[motherduck] MOTHERDUCK_TOKEN not set — skipping publish")
        return 0

    local = Path(local_path or s.duckdb_path)
    if not local.exists():
        logger.error("[motherduck] local warehouse missing: %s", local)
        return 0

    local_con = duckdb.connect(str(local), read_only=True)
    md_con = duckdb.connect(s.motherduck_connection)
    try:
        objects = _local_objects(local_con)
        if not objects:
            logger.warning("[motherduck] no tables found in %s", local)
            return 0
        for schema in _LOCAL_SCHEMAS:
            md_con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

        published = 0
        for schema, table in objects:
            arrow = local_con.execute(f'SELECT * FROM "{schema}"."{table}"').fetch_arrow_table()
            md_con.register("tmp_df", arrow)
            md_con.execute(f'CREATE OR REPLACE TABLE "{schema}"."{table}" AS SELECT * FROM tmp_df')
            md_con.unregister("tmp_df")
            published += 1
            logger.info("[motherduck] %s.%s (%s rows)", schema, table, arrow.num_rows)
        logger.info("[motherduck] published %s tables → %s", published, s.motherduck_database)
        return published
    finally:
        local_con.close()
        md_con.close()


def main() -> int:
    try:
        publish()
    except Exception as exc:  # noqa: BLE001 - never fail the whole workflow
        logger.error("[motherduck] publish failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
