"""Public holiday collection using the ``holidays`` package.

Holidays are a dimension used to detect seasonal/peak travel patterns and to
explain delay spikes around bank holidays. This collector has no network
dependency — the ``holidays`` library ships with an embedded calendar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import holidays as holidays_lib

from config.logging import logger
from config.settings import Settings
from ingestion.base import Collector

COUNTRIES = [
    "DE",
    "GB",
    "FR",
    "NL",
    "ES",
    "IT",
    "AT",
    "CH",
    "PT",
    "BE",
    "IE",
    "SE",
    "DK",
    "NO",
    "FI",
    "PL",
]


class HolidayCollector(Collector):
    """Collects country-level public holiday calendars."""

    name = "holidays"
    source = "holidays"

    def __init__(self, app_settings: Settings | None = None, years: int | None = None) -> None:
        super().__init__(app_settings)
        self.years = years or self._default_years()

    @staticmethod
    def _default_years() -> list[int]:
        now = datetime.now(UTC)
        return [now.year - 1, now.year, now.year + 1]

    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for country in COUNTRIES:
            try:
                cal = holidays_lib.country_holidays(country, years=self.years)
            except NotImplementedError:
                logger.warning("[holidays] unsupported country %s, skipping", country)
                continue
            for date, name in cal.items():
                rows.append(
                    {
                        "country": country,
                        "date": date.isoformat(),
                        "name": name,
                        "source": "holidays-library",
                        "collected_at": datetime.now(UTC).isoformat(),
                    }
                )
        return self._mock(rows) if self.settings.mock_mode else rows


if __name__ == "__main__":
    collector = HolidayCollector()
    count = collector.run()
    logger.info("Collected %s holiday records", count)
