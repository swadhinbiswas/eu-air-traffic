"""Base collector contract for all ingestion sources.

Every source (airports, flights, weather, holidays, fuel) implements a small
``collect()`` interface that:

1. Computes the window to fetch based on the last watermark (incremental).
2. Pulls records from the upstream API (with retries + rate limiting).
3. Persists raw records (immutable Bronze) as JSON lines.
4. Advances the watermark only after a successful write (checkpointing).

Collectors never throw when an upstream is unavailable: they either fall back
to deterministic synthetic data (``MOCK_MODE``) or raise a typed
:class:`IngestionError` that the orchestrator handles gracefully.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Generic, TypeVar

from config.logging import logger
from config.settings import Settings, settings

T = TypeVar("T")

_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


class IngestionError(RuntimeError):
    """Raised when a source cannot be collected (even in mock mode)."""


class CheckpointStore:
    """Reads/writes JSON watermark files used for incremental loading."""

    def __init__(self, checkpoint_dir: Path) -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
        return self.checkpoint_dir / f"{safe}.json"

    def read(self, name: str, default: datetime = _EPOCH) -> datetime:
        path = self.path(name)
        if not path.exists():
            return default
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return datetime.fromisoformat(payload["watermark"]).astimezone(UTC)
        except (KeyError, ValueError, OSError) as exc:
            logger.warning("Unreadable checkpoint %s, resetting to default: %s", path, exc)
            return default

    def write(self, name: str, watermark: datetime) -> None:
        payload = {
            "watermark": watermark.astimezone(UTC).isoformat(),
            "written_at": datetime.now(UTC).isoformat(),
        }
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)


class Collector(ABC, Generic[T]):  # noqa: UP046
    """Base class shared by all ingestion collectors."""

    name: str = "base"
    source: str = "unknown"

    def __init__(self, app_settings: Settings | None = None) -> None:
        self.settings = app_settings or settings
        self.checkpoints = CheckpointStore(self.settings.checkpoint_dir)

    # ── Public API ─────────────────────────────────────────────────────────
    def run(self, window_hours: int | None = None) -> int:
        """Collect and persist a batch of records. Returns the record count.

        Uses the last watermark as the window start when ``window_hours`` is
        ``None`` (incremental), otherwise a fixed look-back window.
        """
        start = self._window_start(window_hours)
        records = self.fetch(start)
        count = len(records)
        if count:
            self.write(records)
        else:
            logger.info("[%s] no new records in window %s→%s", self.name, start, datetime.now(UTC))
        return count

    # ── Hook points ────────────────────────────────────────────────────────
    @abstractmethod
    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        """Return raw records collected since ``start`` (already UTC-aware)."""

    def write(self, records: Iterable[dict[str, Any]]) -> int:
        """Persist raw records to the Bronze layer. Returns rows written."""
        rows = list(records)
        if not rows:
            return 0

        now = datetime.now(UTC)
        stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
        out_dir = self.settings.bronze_dir / self.source / now.strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / f"{self.name}_{stamp}_{self._digest(rows)}.jsonl"

        with file_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

        # Advance watermark only after the file is on disk.
        self.checkpoints.write(self.name, now)
        logger.info("[%s] wrote %s raw rows → %s", self.name, len(rows), file_path)
        return len(rows)

    # ── Helpers ────────────────────────────────────────────────────────────
    def _window_start(self, window_hours: int | None) -> datetime:
        if window_hours is not None:
            return datetime.now(UTC) - timedelta(hours=window_hours)
        return self.checkpoints.read(self.name)

    @staticmethod
    def _digest(rows: list[dict[str, Any]]) -> str:
        blob = hashlib.sha256()
        for row in rows[:10]:
            blob.update(json.dumps(row, sort_keys=True, default=str).encode("utf-8"))
        return blob.hexdigest()[:10]

    @staticmethod
    def _utc(epoch: float | None) -> datetime | None:
        if epoch is None:
            return None
        try:
            return datetime.fromtimestamp(float(epoch), tz=UTC)
        except (ValueError, OverflowError, OSError):
            return None

    def _throttle(self) -> None:
        if self.settings.rate_limit_delay_seconds > 0:
            time.sleep(self.settings.rate_limit_delay_seconds)

    def _mock(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Route to synthetic data when credentials are missing and mock enabled."""
        if self.settings.mock_mode:
            from ingestion.synthetic import synthetic_for  # local import avoids cycles

            return synthetic_for(self.source, rows)
        return rows
