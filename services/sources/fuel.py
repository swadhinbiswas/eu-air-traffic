"""Jet-fuel price series.

Uses AviationStack when an API key is configured, otherwise the deterministic
regional fallback series from the existing fuel collector. Low frequency — this
feeds decision data and the Gold fuel mart, not the live map.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from services.sources.base import Source


class FuelSource(Source):
    """Daily jet-fuel prices by region."""

    name = "fuel"
    key = "series_key"

    def fetch(self) -> list[dict[str, Any]]:
        # Reuse the battle-tested fuel collector (remote + fallback series).
        from ingestion.fuel.collector import FuelCollector

        collector = FuelCollector(self.settings)
        start = datetime.now(UTC) - timedelta(days=365)
        try:
            rows = collector.fetch(start)
        except Exception as exc:  # noqa: BLE001 - never break the collector loop
            from config.logging import logger

            logger.warning("[fuel] fetch failed: %s", exc)
            return []

        collected_at = datetime.now(UTC).isoformat()
        for row in rows:
            row.setdefault("source", "aviationstack")
            row["collected_at"] = collected_at
            row["series_key"] = f"{row.get('date')}_{row.get('region')}"
        return rows
