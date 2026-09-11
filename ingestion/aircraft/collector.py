"""Aircraft fleet data collection.

Collects aircraft fleet information including aircraft types, registrations,
airlines, and technical specifications. Uses the OpenFlights aircraft database
as a primary source with fallback to synthetic data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings, settings
from ingestion.base import Collector
from ingestion.utils import retry

# European airline ICAOs for filtering
EUROPEAN_AIRLINES = {
    "BAW": "British Airways",
    "AFR": "Air France",
    "DLH": "Lufthansa",
    "KLM": "KLM",
    "IBE": "Iberia",
    "RYR": "Ryanair",
    "EZY": "EasyJet",
    "EIN": "Aer Lingus",
    "SWR": "Swiss",
    "AUA": "Austrian",
    "THY": "Turkish Airlines",
    "SAS": "SAS",
    "FIN": "Finnair",
    "LOT": "LOT Polish",
    "CSA": "Czech Airlines",
    "TAP": "TAP Air Portugal",
    "ELL": "Nordica",
    "ICE": "Icelandair",
    "SIA": "Singapore Airlines",
    "UAE": "Emirates",
    "QTR": "Qatar Airways",
    "ELY": "El Al",
    "RAM": "Royal Air Maroc",
    "TUN": "Tunisair",
}

# Common aircraft types in European fleet
AIRCRAFT_TYPES = {
    "A320": {
        "manufacturer": "Airbus",
        "family": "A320",
        "engine": "jet",
        "capacity": 180,
        "range_km": 6100,
    },
    "A321": {
        "manufacturer": "Airbus",
        "family": "A320",
        "engine": "jet",
        "capacity": 220,
        "range_km": 5900,
    },
    "A319": {
        "manufacturer": "Airbus",
        "family": "A320",
        "engine": "jet",
        "capacity": 156,
        "range_km": 6800,
    },
    "A320N": {
        "manufacturer": "Airbus",
        "family": "A320",
        "engine": "jet",
        "capacity": 186,
        "range_km": 6300,
    },
    "A321N": {
        "manufacturer": "Airbus",
        "family": "A320",
        "engine": "jet",
        "capacity": 232,
        "range_km": 7400,
    },
    "A332": {
        "manufacturer": "Airbus",
        "family": "A330",
        "engine": "jet",
        "capacity": 277,
        "range_km": 13400,
    },
    "A333": {
        "manufacturer": "Airbus",
        "family": "A330",
        "engine": "jet",
        "capacity": 277,
        "range_km": 11750,
    },
    "A359": {
        "manufacturer": "Airbus",
        "family": "A350",
        "engine": "jet",
        "capacity": 325,
        "range_km": 15000,
    },
    "A35K": {
        "manufacturer": "Airbus",
        "family": "A350",
        "engine": "jet",
        "capacity": 366,
        "range_km": 16100,
    },
    "A388": {
        "manufacturer": "Airbus",
        "family": "A380",
        "engine": "jet",
        "capacity": 555,
        "range_km": 15200,
    },
    "B737": {
        "manufacturer": "Boeing",
        "family": "737",
        "engine": "jet",
        "capacity": 189,
        "range_km": 6500,
    },
    "B738": {
        "manufacturer": "Boeing",
        "family": "737",
        "engine": "jet",
        "capacity": 189,
        "range_km": 5700,
    },
    "B739": {
        "manufacturer": "Boeing",
        "family": "737",
        "engine": "jet",
        "capacity": 215,
        "range_km": 5600,
    },
    "B748": {
        "manufacturer": "Boeing",
        "family": "747",
        "engine": "jet",
        "capacity": 410,
        "range_km": 14800,
    },
    "B752": {
        "manufacturer": "Boeing",
        "family": "757",
        "engine": "jet",
        "capacity": 239,
        "range_km": 7200,
    },
    "B763": {
        "manufacturer": "Boeing",
        "family": "767",
        "engine": "jet",
        "capacity": 269,
        "range_km": 11000,
    },
    "B772": {
        "manufacturer": "Boeing",
        "family": "777",
        "engine": "jet",
        "capacity": 313,
        "range_km": 14200,
    },
    "B773": {
        "manufacturer": "Boeing",
        "family": "777",
        "engine": "jet",
        "capacity": 396,
        "range_km": 11100,
    },
    "B77W": {
        "manufacturer": "Boeing",
        "family": "777",
        "engine": "jet",
        "capacity": 396,
        "range_km": 13600,
    },
    "B788": {
        "manufacturer": "Boeing",
        "family": "787",
        "engine": "jet",
        "capacity": 330,
        "range_km": 14100,
    },
    "B789": {
        "manufacturer": "Boeing",
        "family": "787",
        "engine": "jet",
        "capacity": 381,
        "range_km": 14000,
    },
    "B78X": {
        "manufacturer": "Boeing",
        "family": "787",
        "engine": "jet",
        "capacity": 440,
        "range_km": 13600,
    },
    "CRJ9": {
        "manufacturer": "Bombardier",
        "family": "CRJ",
        "engine": "jet",
        "capacity": 90,
        "range_km": 2800,
    },
    "E190": {
        "manufacturer": "Embraer",
        "family": "E-Jet",
        "engine": "jet",
        "capacity": 114,
        "range_km": 4500,
    },
    "E195": {
        "manufacturer": "Embraer",
        "family": "E-Jet",
        "engine": "jet",
        "capacity": 124,
        "range_km": 4300,
    },
    "AT76": {
        "manufacturer": "ATR",
        "family": "ATR 72",
        "engine": "turboprop",
        "capacity": 78,
        "range_km": 1500,
    },
    "AT75": {
        "manufacturer": "ATR",
        "family": "ATR 72",
        "engine": "turboprop",
        "capacity": 74,
        "range_km": 1500,
    },
}


class AircraftCollector(Collector):
    """Collects aircraft fleet data for European airlines."""

    name = "aircraft"
    source = "aircraft"

    OPENFLIGHTS_AIRCRAFT_URL = (
        "https://raw.githubusercontent.com/jpatokal/openflights/master/data/planes.dat"
    )

    def __init__(self, app_settings: Settings | None = None) -> None:
        super().__init__(app_settings)

    @retry()
    def _fetch_remote(self, url: str) -> str:
        response = requests.get(url, timeout=settings.request_timeout_seconds)
        response.raise_for_status()
        return response.text

    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        # Near-static data: prefer the committed local reference (no network).
        from ingestion import reference

        local = reference.fleet()
        if local:
            now = datetime.now(UTC).isoformat()
            return self._mock(
                [
                    {
                        "type_iata": row.get("type_iata") or "",
                        "type_icao": row["type_icao"],
                        "manufacturer": row.get("manufacturer"),
                        "family": row.get("family"),
                        "engine": row.get("engine"),
                        "capacity": row.get("capacity"),
                        "range_km": row.get("range_km"),
                        "source": "openflights_cache",
                        "collected_at": now,
                    }
                    for row in local
                ]
            )

        # Cache miss: fetch once from OpenFlights.
        try:
            data = self._fetch_remote(self.OPENFLIGHTS_AIRCRAFT_URL)
            for line in data.strip().split("\n"):
                try:
                    parts = line.split(",")
                    if len(parts) >= 3:
                        iata = parts[1].strip('"')
                        icao = parts[2].strip('"')

                        # Look up aircraft type details
                        type_info = self._lookup_type(icao)
                        if type_info:
                            rows.append(
                                {
                                    "type_iata": iata,
                                    "type_icao": icao,
                                    "manufacturer": type_info["manufacturer"],
                                    "family": type_info["family"],
                                    "engine": type_info["engine"],
                                    "capacity": type_info["capacity"],
                                    "range_km": type_info["range_km"],
                                    "source": "openflights",
                                    "collected_at": datetime.now(UTC).isoformat(),
                                }
                            )
                except Exception as exc:
                    logger.debug("[aircraft] Skipping malformed line: %s", exc)
        except Exception as exc:
            logger.warning("[aircraft] Remote fetch failed (%s); using fallback", exc)

        # Fallback to synthetic fleet data
        if not rows:
            rows = self._fallback_fleet()

        return self._mock(rows) if self.settings.mock_mode else rows

    def _lookup_type(self, icao: str) -> dict[str, Any] | None:
        """Look up aircraft type details from our reference data."""
        return AIRCRAFT_TYPES.get(icao)

    def _fallback_fleet(self) -> list[dict[str, Any]]:
        """Generate fallback fleet data."""
        rows = []
        for icao, info in AIRCRAFT_TYPES.items():
            rows.append(
                {
                    "type_iata": icao[:3],
                    "type_icao": icao,
                    "manufacturer": info["manufacturer"],
                    "family": info["family"],
                    "engine": info["engine"],
                    "capacity": info["capacity"],
                    "range_km": info["range_km"],
                    "source": "reference",
                    "collected_at": datetime.now(UTC).isoformat(),
                }
            )
        return rows


if __name__ == "__main__":
    collector = AircraftCollector()
    count = collector.run()
    logger.info("Collected %s aircraft records", count)
