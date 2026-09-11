"""Publish site-facing tables to MotherDuck (stories, ops, lineage).

The dashboard reads Gold analytics straight from MotherDuck at runtime — no
static bundle, no API. These three small tables carry the precomputed pieces
that can't be a plain SQL view: the narrative story cards, the pipeline/quality
reports, and the dbt lineage graph. All are derived and overwritten every run.

Tables (schema ``main``):
* ``site_stories``  — one row per story card (``chart_json`` holds the chart)
* ``site_ops``      — single row (``key='latest'``) with pipeline+quality JSON
* ``site_lineage``  — single row (``key='lineage'``) with the dbt DAG JSON

    python -m scripts.publish_site_tables
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from config.logging import logger
from config.settings import settings
from scripts.build_web_bundle import _build_stories, _parse_dbt_lineage, _scalar


def _open_local() -> duckdb.DuckDBPyConnection | None:
    path = Path(settings.duckdb_path)
    if not path.exists():
        logger.warning("[site-tables] local warehouse missing: %s", path)
        return None
    return duckdb.connect(str(path), read_only=True)


def _checkpoint(name: str) -> Any:
    path = settings.checkpoint_dir / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def publish() -> dict[str, int]:
    """Write the site tables to MotherDuck. Returns row counts per table."""
    if not settings.motherduck_enabled:
        logger.warning("[site-tables] MOTHERDUCK_TOKEN not set — skipping")
        return {}

    local = _open_local()
    stories: list[dict[str, Any]] = []
    if local is not None:
        try:
            position_count = _scalar(local, "SELECT COUNT(*) FROM fact_positions", 0) or 0
            stories = _build_stories(local, int(position_count), 0.0)
        finally:
            local.close()

    ops = {
        "pipeline": _checkpoint("pipeline_report.json"),
        "quality": _checkpoint("quality_report.json"),
    }
    lineage = _parse_dbt_lineage()

    md = duckdb.connect(settings.motherduck_connection)
    try:
        md.execute(
            "CREATE TABLE IF NOT EXISTS site_stories ("
            "id VARCHAR, category VARCHAR, tone VARCHAR, title VARCHAR, "
            "metric VARCHAR, unit VARCHAR, narrative VARCHAR, chart_json VARCHAR)"
        )
        md.execute("DELETE FROM site_stories")
        for story in stories:
            md.execute(
                "INSERT INTO site_stories VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    story.get("id"),
                    story.get("category"),
                    story.get("tone"),
                    story.get("title"),
                    story.get("metric"),
                    story.get("unit"),
                    story.get("narrative"),
                    json.dumps(story.get("chart"), default=str),
                ],
            )

        md.execute(
            "CREATE TABLE IF NOT EXISTS site_ops "
            "(key VARCHAR, payload_json VARCHAR, updated_at TIMESTAMP)"
        )
        md.execute("DELETE FROM site_ops WHERE key = 'latest'")
        md.execute(
            "INSERT INTO site_ops VALUES ('latest', ?, ?)",
            [json.dumps(ops, default=str), datetime.now(UTC)],
        )

        md.execute(
            "CREATE TABLE IF NOT EXISTS site_lineage "
            "(key VARCHAR, payload_json VARCHAR, updated_at TIMESTAMP)"
        )
        md.execute("DELETE FROM site_lineage WHERE key = 'lineage'")
        md.execute(
            "INSERT INTO site_lineage VALUES ('lineage', ?, ?)",
            [json.dumps(lineage, default=str), datetime.now(UTC)],
        )
    finally:
        md.close()

    counts = {"site_stories": len(stories), "site_ops": 1, "site_lineage": 1}
    logger.info("[site-tables] published → %s: %s", settings.motherduck_database, counts)
    return counts


def main() -> int:
    try:
        publish()
    except Exception as exc:  # noqa: BLE001 - never fail the whole workflow
        logger.error("[site-tables] publish failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
