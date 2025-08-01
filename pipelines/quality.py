"""Data Quality framework — measures validation outcomes across the Medallion layers.

For every registered source the quality module computes:
- ``bronze_rows``   : raw rows landed by the collectors.
- ``silver_rows``   : rows that passed validation and are available downstream.
- ``quarantined_rows`` : rows sent to the dead-letter queue (failed validation).
- ``pass_rate``     : silver / (silver + quarantined) — the share that made it through.
- ``freshness``     : how long since the latest record / run in the source.

The results are written to ``warehouse/checkpoints/quality_report.json`` so the
pipeline report, the API (``GET /quality/report``) and CI all read the same
source of truth. Validation never blocks a run — it measures what already
happened during the Silver layer.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import polars as pl

from config.logging import logger
from config.settings import Settings, settings

SOURCES = ("airports", "flights", "weather", "holidays", "fuel")


def _bronze_rows(s: Settings, source: str) -> int:
    """Count raw rows persisted to the Bronze Parquet area for a source."""
    source_dir = s.bronze_dir / "parquet" / source
    if not source_dir.exists():
        return 0
    total = 0
    for path in source_dir.glob("*.parquet"):
        try:
            total += pl.read_parquet(path).height
        except Exception:  # noqa: BLE001 - best-effort metric collection
            logger.debug("[quality] could not read bronze %s", path)
    return total


def _quarantine_rows(s: Settings, source: str) -> int:
    """Count rows sitting in the source's dead-letter queue."""
    qdir = s.quarantine_dir / source
    if not qdir.exists():
        return 0
    total = 0
    for path in qdir.glob("*.parquet"):
        try:
            total += pl.read_parquet(path).height
        except Exception:  # noqa: BLE001 - best-effort metric collection
            logger.debug("[quality] could not read quarantine %s", path)
    return total


def _silver_path(s: Settings, source: str) -> Path | None:
    if source == "airports":
        path = s.silver_dir / "airports" / "airports.parquet"
    else:
        path = s.silver_dir / source / "data.parquet"
    return path if path.exists() else None


def _silver_rows(s: Settings, source: str) -> int:
    path = _silver_path(s, source)
    if path is None:
        return 0
    try:
        return pl.read_parquet(path).height
    except Exception:  # noqa: BLE001 - best-effort metric collection
        return 0


def _freshness(s: Settings, source: str) -> str | None:
    """ISO timestamp of the newest Silver record, or None when empty."""
    path = _silver_path(s, source)
    if path is None:
        return None
    try:
        df = pl.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    if df.is_empty():
        return None
    for col in ("ingestion_date", "collected_at", "timestamp", "date"):
        if col in df.columns:
            value = df[col].max()
            if value is None:
                continue
            return str(value)
    return None


def quality_report(app_settings: Settings | None = None) -> dict[str, Any]:
    """Compute per-source quality metrics and an overall summary."""
    s = app_settings or settings
    sources: dict[str, Any] = {}
    overall_bronze = overall_silver = overall_quarantine = 0

    for source in SOURCES:
        bronze = _bronze_rows(s, source)
        silver = _silver_rows(s, source)
        quarantined = _quarantine_rows(s, source)
        pass_rate = round(silver / (silver + quarantined), 4) if (silver + quarantined) else None

        overall_bronze += bronze
        overall_silver += silver
        overall_quarantine += quarantined

        sources[source] = {
            "bronze_rows": bronze,
            "silver_rows": silver,
            "quarantined_rows": quarantined,
            "pass_rate": pass_rate,
            "freshness": _freshness(s, source),
        }

    overall_pass = (
        round(overall_silver / (overall_silver + overall_quarantine), 4)
        if (overall_silver + overall_quarantine)
        else None
    )

    report = {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "environment": s.environment,
        "summary": {
            "bronze_rows": overall_bronze,
            "silver_rows": overall_silver,
            "quarantined_rows": overall_quarantine,
            "overall_pass_rate": overall_pass,
            "sources_with_data": sum(1 for v in sources.values() if v["silver_rows"] > 0),
        },
        "sources": sources,
    }

    path = s.checkpoint_dir / "quality_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(report), encoding="utf-8")
    logger.info("[quality] report written → %s", path)
    return report


def _render(report: dict[str, Any]) -> str:
    import json

    return json.dumps(report, indent=2, default=str)


def load_report(app_settings: Settings | None = None) -> dict[str, Any] | None:
    """Load the last persisted quality report (for the API)."""
    s = app_settings or settings
    path = s.checkpoint_dir / "quality_report.json"
    if not path.exists():
        return None
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import json

    print(json.dumps(quality_report(), indent=2, default=str))
