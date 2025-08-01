"""Bronze layer — persist collected JSONL into immutable, partitioned Parquet.

The Bronze layer is our "single source of truth": raw records exactly as
received from collectors, never mutated in place. Each batch becomes a dated
Parquet file, so re-running the pipeline never overwrites history.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from config.logging import logger
from config.settings import Settings, settings


def _is_jsonl(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".jsonl", ".ndjson"}


def read_jsonl(path: Path) -> pl.DataFrame:
    """Read a JSONL file into a Polars DataFrame (best-effort schema)."""
    try:
        return pl.read_ndjson(path, infer_schema_length=1_000_000)
    except Exception:  # noqa: BLE001 - heterogeneous/hand-made rows fall back below
        # Fall back to manual parsing for heterogeneous rows.
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if not rows:
            return pl.DataFrame()
        return pl.DataFrame(rows)


def jsonl_files(source: str, app_settings: Settings | None = None) -> list[Path]:
    """All raw JSONL files collected for a source, newest first."""
    s = app_settings or settings
    source_dir = s.bronze_dir / source
    if not source_dir.exists():
        return []
    files = sorted((p for p in source_dir.rglob("*") if _is_jsonl(p)), reverse=True)
    return files


def persist_source(source: str, app_settings: Settings | None = None) -> int:
    """Append all JSONL batches for a source into a partitioned Parquet store.

    Each raw JSONL file maps 1:1 to a Parquet file with the same stem, so
    re-running the pipeline is idempotent (already-persisted files are skipped).

    Returns the total number of rows persisted for this source.
    """
    s = app_settings or settings
    files = jsonl_files(source, s)
    if not files:
        logger.info("[bronze] no raw files for source=%s", source)
        return 0

    out_dir = s.bronze_dir / "parquet" / source
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for path in files:
        target = out_dir / f"{path.stem}.parquet"
        if target.exists():
            continue
        df = read_jsonl(path)
        if df.is_empty():
            continue

        # Ensure a stable, queryable ingestion date column.
        if "ingestion_date" not in df.columns:
            df = df.with_columns(pl.lit(path.parent.name).alias("ingestion_date"))

        df.write_parquet(target, compression="zstd")
        total += df.height

    logger.info("[bronze] source=%s persisted %s rows to %s", source, total, out_dir)
    return total


def persist_all(
    sources: list[str] | None = None, app_settings: Settings | None = None
) -> dict[str, int]:
    """Persist all registered (or the given) sources to Bronze Parquet."""
    from ingestion.registry import available

    targets = sources or available()
    counts: dict[str, int] = {}
    for source in targets:
        counts[source] = persist_source(source, app_settings)
    return counts


if __name__ == "__main__":
    logger.info("Bronze persist results: %s", persist_all())
