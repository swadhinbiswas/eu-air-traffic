"""Fuel price collection.

Jet fuel prices are notoriously hard to get for free. This collector reads a
configurable source (defaults to a lightweight reference endpoint) and falls
back to deterministic values so the ``fact_delays`` / fuel correlation marts
still build. Point ``FUEL_PRICES_URL`` at any endpoint returning
``[{date, price_per_litre, region}]`` to plug in a paid/proprietary feed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings, settings
from ingestion.base import Collector
from ingestion.utils import retry, to_float

DEFAULT_REGIONS = ("EU", "ME", "NA")


class FuelCollector(Collector):
    """Collects jet fuel price series, per region per day."""

    name = "fuel"
    source = "fuel"

    def __init__(self, app_settings: Settings | None = None, url: str | None = None) -> None:
        super().__init__(app_settings)
        self.url = (
            url
            or settings.aviationstack_api_key
            and (
                "https://api.aviationstack.com/v1/prices?access_key="
                f"{settings.aviationstack_api_key}&limit=100"
            )
        )

    @retry()
    def _fetch_remote(self, url: str) -> Any:
        response = requests.get(url, timeout=settings.request_timeout_seconds)
        response.raise_for_status()
        return response.json()

    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.url:
            try:
                payload = self._fetch_remote(self.url)
                data = payload.get("data", payload)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            rows.append(self._normalise(item))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[fuel] remote fetch failed (%s); using fallback series", exc)

        if not rows:
            rows = self._fallback_series(start)
        return self._mock(rows) if self.settings.mock_mode else rows

    @staticmethod
    def _normalise(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": item.get("date") or datetime.now(UTC).date().isoformat(),
            "region": item.get("region", "EU"),
            "price_per_litre": to_float(item.get("price_per_litre") or item.get("price")),
            "currency": item.get("currency", "EUR"),
            "source": "remote",
            "collected_at": datetime.now(UTC).isoformat(),
        }

    def _fallback_series(self, start: datetime) -> list[dict[str, Any]]:
        today = datetime.now(UTC).date()
        rows: list[dict[str, Any]] = []
        for offset in range(max((today - start.date()).days, 0) + 1):
            day = today - timedelta(days=offset)
            for region in DEFAULT_REGIONS:
                rows.append(
                    {
                        "date": day.isoformat(),
                        "region": region,
                        "price_per_litre": 1.10 + (offset % 5) * 0.02,
                        "currency": "EUR",
                        "source": "reference",
                        "collected_at": datetime.now(UTC).isoformat(),
                    }
                )
        return rows


if __name__ == "__main__":
    collector = FuelCollector()
    count = collector.run()
    logger.info("Collected %s fuel records", count)
