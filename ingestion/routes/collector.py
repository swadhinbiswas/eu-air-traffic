"""Route network data collection.

Collects airline route information including origin-destination pairs,
distances, and equipment types. Uses OpenFlights routes database as
primary source with fallback to synthetic data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings, settings
from ingestion.base import Collector
from ingestion.utils import retry

# Major European airports for route generation
EUROPEAN_AIRPORTS: dict[str, dict[str, float | str]] = {
    "EGLL": {"name": "London Heathrow", "lat": 51.47, "lon": -0.45},
    "EDDF": {"name": "Frankfurt", "lat": 50.03, "lon": 8.54},
    "LFPG": {"name": "Paris CDG", "lat": 49.01, "lon": 2.55},
    "EHAM": {"name": "Amsterdam", "lat": 52.31, "lon": 4.77},
    "LEMD": {"name": "Madrid", "lat": 40.47, "lon": -3.56},
    "LIRF": {"name": "Rome FCO", "lat": 41.80, "lon": 12.25},
    "EDDM": {"name": "Munich", "lat": 48.35, "lon": 11.79},
    "LEBL": {"name": "Barcelona", "lat": 41.29, "lon": 2.08},
    "LTFM": {"name": "Istanbul", "lat": 41.26, "lon": 28.74},
    "LSZH": {"name": "Zurich", "lat": 47.46, "lon": 8.55},
    "LOWW": {"name": "Vienna", "lat": 48.11, "lon": 16.57},
    "EKCH": {"name": "Copenhagen", "lat": 55.62, "lon": 12.66},
    "ENGM": {"name": "Oslo", "lat": 60.19, "lon": 11.10},
    "ESSA": {"name": "Stockholm", "lat": 59.65, "lon": 17.92},
    "EFHK": {"name": "Helsinki", "lat": 60.32, "lon": 24.96},
    "EPWA": {"name": "Warsaw", "lat": 52.17, "lon": 20.97},
    "LKPR": {"name": "Prague", "lat": 50.10, "lon": 14.26},
    "LHBP": {"name": "Budapest", "lat": 47.44, "lon": 19.26},
    "LIMC": {"name": "Milan MXP", "lat": 45.63, "lon": 8.73},
    "LGAV": {"name": "Athens", "lat": 37.94, "lon": 23.94},
    "LPPT": {"name": "Lisbon", "lat": 38.78, "lon": -9.14},
    "EINN": {"name": "Dublin", "lat": 53.43, "lon": -6.25},
    "EGCC": {"name": "Manchester", "lat": 53.35, "lon": -2.27},
}


class RouteCollector(Collector):
    """Collects airline route network data."""

    name = "routes"
    source = "routes"

    OPENFLIGHTS_ROUTES_URL = (
        "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"
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

        local = reference.routes()
        if local:
            now = datetime.now(UTC).isoformat()
            return self._mock(
                [
                    {
                        "airline": row["airline"],
                        "origin": row["origin"],
                        "destination": row["destination"],
                        "stops": row.get("stops", 0),
                        "equipment": row.get("equipment", ""),
                        "distance_km": self._estimate_distance(row["origin"], row["destination"]),
                        "source": "openflights_cache",
                        "collected_at": now,
                    }
                    for row in local
                ]
            )

        # Cache miss: fetch once from OpenFlights.
        try:
            from ingestion import reference

            known_icao = set(reference.airport_coordinates())
            iata_codes = reference.iata_to_icao()
            data = self._fetch_remote(self.OPENFLIGHTS_ROUTES_URL)
            for line in data.strip().split("\n"):
                try:
                    parts = line.split(",")
                    if len(parts) >= 8:
                        airline = parts[0].strip('"')
                        # routes.dat endpoints are IATA; the airport index is ICAO.
                        origin = iata_codes.get(parts[2].strip('"').upper(), parts[2].strip('"'))
                        dest = iata_codes.get(parts[4].strip('"').upper(), parts[4].strip('"'))
                        stops = parts[5].strip('"')
                        equipment = parts[7].strip('"')

                        # Filter for European routes
                        if origin in known_icao or dest in known_icao:
                            rows.append(
                                {
                                    "airline": airline,
                                    "origin": origin,
                                    "destination": dest,
                                    "stops": int(stops) if stops.isdigit() else 0,
                                    "equipment": equipment,
                                    "distance_km": self._estimate_distance(origin, dest),
                                    "source": "openflights",
                                    "collected_at": datetime.now(UTC).isoformat(),
                                }
                            )
                except Exception as exc:
                    logger.debug("[routes] Skipping malformed line: %s", exc)
        except Exception as exc:
            logger.warning("[routes] Remote fetch failed (%s); using fallback", exc)

        # Fallback to synthetic route network
        if not rows:
            rows = self._fallback_routes()

        return self._mock(rows) if self.settings.mock_mode else rows

    def _estimate_distance(self, origin: str, dest: str) -> float | None:
        """Great-circle distance, resolved against the full bundled airport list."""
        from ingestion import reference

        return reference.route_distance_km(origin, dest)

    def _fallback_routes(self) -> list[dict[str, Any]]:
        """Generate synthetic European route network."""
        rows = []
        airports = list(EUROPEAN_AIRPORTS.keys())

        # Generate hub-to-hub routes
        hubs = ["EGLL", "EDDF", "LFPG", "EHAM", "LEMD", "LIRF"]
        for hub in hubs:
            for airport in airports:
                if airport != hub:
                    rows.append(
                        {
                            "airline": "MULTI",
                            "origin": hub,
                            "destination": airport,
                            "stops": 0,
                            "equipment": "A320",
                            "distance_km": self._estimate_distance(hub, airport),
                            "source": "synthetic",
                            "collected_at": datetime.now(UTC).isoformat(),
                        }
                    )

        return rows


if __name__ == "__main__":
    collector = RouteCollector()
    count = collector.run()
    logger.info("Collected %s route records", count)
