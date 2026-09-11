"""Bronze layer writer for the collector service.

Lands raw records as immutable JSON Lines under ``warehouse/bronze/<source>/``,
using the same partitioned layout as the batch collectors so the existing
Bronze → Parquet step and Synced-to-Hugging-Face flow work unchanged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.logging import logger
from config.settings import Settings, settings


class BronzeWriter:
    """Append-only writer for raw collector payloads."""

    def __init__(self, app_settings: Settings | None = None) -> None:
        self.settings = app_settings or settings
        self.written: list[Path] = []

    @staticmethod
    def _digest(records: list[dict[str, Any]]) -> str:
        blob = hashlib.sha256()
        for record in records[:10]:
            blob.update(json.dumps(record, sort_keys=True, default=str).encode("utf-8"))
        return blob.hexdigest()[:10]

    def write(self, source: str, records: list[dict[str, Any]]) -> Path | None:
        """Persist a batch of raw records. Returns the file written."""
        if not records:
            return None

        now = datetime.now(UTC)
        stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
        out_dir = self.settings.bronze_dir / source / now.strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{source}_{stamp}_{self._digest(records)}.jsonl"

        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        self.written.append(path)
        logger.info("[bronze] source=%s rows=%s → %s", source, len(records), path.name)
        return path

    def pending_files(self) -> list[Path]:
        """Files written since the last :meth:`mark_synced`."""
        return list(self.written)

    def mark_synced(self) -> None:
        self.written.clear()
