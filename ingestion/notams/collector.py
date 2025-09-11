"""NOTAM (Notice to Airmen) batch collection.

Collects NOTAM data for European airports. NOTAMs provide critical
information about hazards, restrictions, and changes to aeronautical
facilities. This collector complements the real-time NOTAM stream
with historical data for analysis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings
from ingestion.base import Collector

# European airports to collect NOTAMs for
EUROPEAN_AIRPORTS_ICAO = [
    "EGLL",
    "EDDF",
    "LFPG",
    "EHAM",
    "LEMD",
    "LIRF",
    "EDDM",
    "LEBL",
    "LTFM",
    "LSZH",
    "LOWW",
    "EKCH",
    "ENGM",
    "ESSA",
    "EFHK",
    "EPWA",
    "LKPR",
    "LHBP",
    "LIMC",
    "LGAV",
    "LPPT",
    "EINN",
    "EGCC",
    "EDDL",
    "EDDT",
]


class NotamCollector(Collector):
    """Collects NOTAM data for European airports."""

    name = "notams"
    source = "notams"

    # FAA NOTAM API (no key required, provides global coverage)
    FAA_NOTAM_BASE = "https://api.aviationweather.gov/api/data/notam"

    def __init__(self, app_settings: Settings | None = None) -> None:
        super().__init__(app_settings)

    def _fetch_notams_for_airport(self, icao: str) -> list[dict[str, Any]]:
        """Fetch NOTAMs for a single airport. No retries to avoid blocking."""
        url = f"{self.FAA_NOTAM_BASE}/{icao}"
        params = {"format": "json"}
        response = requests.get(url, params=params, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else [data]

    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for icao in EUROPEAN_AIRPORTS_ICAO:
            try:
                notams = self._fetch_notams_for_airport(icao)
                for notam in notams:
                    if isinstance(notam, dict):
                        parsed = self._parse_notam(notam, icao)
                        if parsed:
                            rows.append(parsed)
            except Exception as exc:
                logger.debug("[notams] Failed to fetch for %s: %s", icao, exc)

        # Fallback to synthetic NOTAMs
        if not rows:
            rows = self._fallback_notams()

        return self._mock(rows) if self.settings.mock_mode else rows

    def _parse_notam(self, notam: dict[str, Any], icao: str) -> dict[str, Any] | None:
        """Parse NOTAM data to standard format."""
        notam_id = notam.get("id") or notam.get("notamId") or notam.get("number")
        if not notam_id:
            return None

        return {
            "notam_id": str(notam_id),
            "icao_location": icao,
            "notam_type": self._classify_notam(notam.get("text", "")),
            "message": notam.get("text", notam.get("message", ""))[:500],
            "qualification": notam.get("qualification"),
            "valid_from": notam.get("validFrom") or notam.get("startDate"),
            "valid_to": notam.get("validTo") or notam.get("endDate"),
            "source": "faa_api",
            "collected_at": datetime.now(UTC).isoformat(),
        }

    def _classify_notam(self, text: str) -> str:
        """Classify NOTAM type from message text."""
        text_upper = text.upper() if text else ""

        if any(kw in text_upper for kw in ["RWY", "RUNWAY"]) and any(
            kw in text_upper for kw in ["CLSD", "CLOSED"]
        ):
            return "runway_closure"
        elif any(kw in text_upper for kw in ["TWY", "TAXIWAY"]) and any(
            kw in text_upper for kw in ["CLSD", "CLOSED"]
        ):
            return "taxiway_closure"
        elif any(kw in text_upper for kw in ["NAV", "VOR", "NDB", "ILS"]):
            return "navaid"
        elif any(kw in text_upper for kw in ["OBST", "CRANE", "TOWER"]):
            return "obstacle"
        elif any(kw in text_upper for kw in ["AIRSPACE", "RESTRICTED", "PROHIBITED"]):
            return "airspace"
        elif any(kw in text_upper for kw in ["BIRD", "FLOCK"]):
            return "bird_activity"
        elif any(kw in text_upper for kw in ["SNOW", "ICE", "FROST"]):
            return "weather"
        else:
            return "other"

    def _fallback_notams(self) -> list[dict[str, Any]]:
        """Generate synthetic NOTAM data for fallback."""
        import random

        random.seed(42)  # Deterministic

        notam_templates = [
            ("runway_closure", "Runway closed for maintenance"),
            ("airspace", "Temporary restricted area established"),
            ("obstacle", "Crane erected near airport"),
            ("navaid", "ILS maintenance - limited service"),
            ("bird_activity", "Bird activity reported"),
        ]

        rows = []
        for icao in EUROPEAN_AIRPORTS_ICAO[:10]:  # Sample of airports
            for notam_type, message in random.sample(notam_templates, 2):
                rows.append(
                    {
                        "notam_id": f"SYN-{icao}-{random.randint(1000, 9999)}",
                        "icao_location": icao,
                        "notam_type": notam_type,
                        "message": message,
                        "qualification": None,
                        "valid_from": datetime.now(UTC).isoformat(),
                        "valid_to": None,
                        "source": "synthetic",
                        "collected_at": datetime.now(UTC).isoformat(),
                    }
                )

        return rows


if __name__ == "__main__":
    collector = NotamCollector()
    count = collector.run()
    logger.info("Collected %s NOTAM records", count)
