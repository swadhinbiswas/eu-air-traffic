"""Reference data: airports, routes, fleet, emission factors and holidays.

Slow-changing data published on a daily cadence. Every record carries a
``_kind`` discriminator (``airport`` / ``route`` / ``aircraft`` / ``emission`` /
``holiday``) so the sink can land one Parquet dataset per kind while keeping a
single Kafka topic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from config.logging import logger
from services.emissions import emission_factors
from services.sources.base import Source

LOOKBACK_DAYS = 30

# Per-kind natural keys so every reference record gets a unique ``id`` (routes
# and aircraft share no common identifier, so a generic key would collapse them).
_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "airport": ("icao", "ident"),
    "route": ("airline", "origin", "destination"),
    "aircraft": ("type_icao", "icao"),
    "holiday": ("country", "date", "name"),
    "emission": ("aircraft_type",),
}


def _record_id(label: str, row: dict[str, Any]) -> str:
    fields = _ID_FIELDS.get(label, ("id",))
    parts = [str(row.get(field, "")) for field in fields]
    return f"{label}:" + ":".join(parts)


class ReferenceSource(Source):
    """Airports, routes, fleet and emission factors for the EU network."""

    name = "reference"
    key = "id"

    def __init__(self, app_settings=None) -> None:
        super().__init__(app_settings)
        self._start = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)

    def _safe_fetch(self, label: str, factory) -> list[dict[str, Any]]:
        try:
            collector = factory(self.settings)
            rows = collector.fetch(self._start)
        except Exception as exc:  # noqa: BLE001 - one source must not kill the batch
            logger.warning("[reference] %s failed: %s", label, exc)
            return []
        collected_at = datetime.now(UTC).isoformat()
        for row in rows:
            row["_kind"] = label
            row["collected_at"] = collected_at
            row["id"] = _record_id(label, row)
        return rows

    def fetch(self) -> list[dict[str, Any]]:
        from ingestion.aircraft.collector import AircraftCollector
        from ingestion.airports.collector import AirportCollector
        from ingestion.holidays.collector import HolidayCollector
        from ingestion.routes.collector import RouteCollector

        rows: list[dict[str, Any]] = []
        rows += self._safe_fetch("airport", AirportCollector)
        rows += self._safe_fetch("route", RouteCollector)
        rows += self._safe_fetch("aircraft", AircraftCollector)
        rows += self._safe_fetch("holiday", HolidayCollector)

        collected_at = datetime.now(UTC).isoformat()
        for factor in emission_factors():
            factor["_kind"] = "emission"
            factor["id"] = _record_id("emission", factor)
            factor["collected_at"] = collected_at
            rows.append(factor)
        return rows
