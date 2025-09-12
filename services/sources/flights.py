"""Flight movements (arrivals/departures) from OpenSky Network.

Polls ``/flights/departure`` and ``/flights/arrival`` for the busiest European
hubs. The combined ``/flights/airport`` endpoint only yields departures, so a
store ends up with one known end per flight; the dedicated endpoints give both
directions and the arrival one normally knows both ends. Requires OpenSky
credentials; without them the source returns an empty list rather than failing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from config.logging import logger
from services.sources.base import Source

OPENSKY = "https://opensky-network.org/api"

HEADERS = {
    "User-Agent": "eu-air-traffic-collector/2.0 (+https://github.com/swadhinbiswas/air-traffic)",
    "Accept": "application/json",
}

# Busiest EU/EEA + near-neighbour hubs, polled for movements.
TOP_AIRPORTS: list[str] = [
    "EDDF",
    "EGLL",
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
    "LROP",
    "LGAV",
    "LPPT",
    "EGCC",
    "EDDL",
    "EDDT",
]


class FlightsSource(Source):
    """Recent arrivals/departures for the major European airports."""

    name = "flights"
    key = "flight_id"

    def __init__(self, app_settings=None, session: requests.Session | None = None) -> None:
        super().__init__(app_settings)
        self._session = session or requests.Session()

    @property
    def _auth_kwargs(self) -> dict[str, Any]:
        from services.opensky_auth import auth_for

        extra_headers, auth = auth_for(self.settings)
        return {"headers": {**HEADERS, **extra_headers}, "auth": auth}

    def _airport_flights(self, icao: str, begin: int, end: int, kind: str) -> list[dict[str, Any]]:
        """``kind`` is ``departure`` or ``arrival``.

        The combined ``/flights/airport`` endpoint only returns departures in
        practice, which left every store as "departure known, arrival unknown".
        The dedicated endpoints return each direction, and the arrival one
        usually knows both ends.
        """
        params: dict[str, str | int] = {"airport": icao, "begin": begin, "end": end}
        try:
            res = self._session.get(
                f"{OPENSKY}/flights/{kind}",
                params=params,
                timeout=self.settings.request_timeout_seconds,
                **self._auth_kwargs,
            )
        except requests.RequestException as exc:
            logger.debug("[flights] opensky %s %s: %s", kind, icao, exc)
            return []
        if res.status_code != 200:
            logger.debug("[flights] opensky %s %s -> HTTP %s", kind, icao, res.status_code)
            return []
        try:
            payload = res.json()
        except ValueError:
            return []
        return payload if isinstance(payload, list) else []

    def fetch(self) -> list[dict[str, Any]]:
        if not self.settings.opensky_username and not self.settings.opensky_client_id:
            logger.debug("[flights] OpenSky credentials unavailable — skipping")
            return []

        end = int(datetime.now(UTC).timestamp())
        begin = int((datetime.now(UTC) - timedelta(minutes=30)).timestamp())
        collected_at = datetime.now(UTC).isoformat()
        airports = TOP_AIRPORTS[: self.settings.flights_airports]
        rows: list[dict[str, Any]] = []

        from concurrent.futures import ThreadPoolExecutor

        def fetch_airport(icao: str) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for kind in ("departure", "arrival"):
                for flight in self._airport_flights(icao, begin, end, kind):
                    icao24 = flight.get("icao24")
                    if not icao24:
                        continue
                    first_seen = flight.get("firstSeen")
                    last_seen = flight.get("lastSeen")
                    # The queried airport is authoritative for its own direction
                    # even when OpenSky omits the estimated field.
                    departure = flight.get("estDepartureAirport")
                    arrival = flight.get("estArrivalAirport")
                    if kind == "departure" and not departure:
                        departure = icao
                    if kind == "arrival" and not arrival:
                        arrival = icao
                    out.append(
                        {
                            "flight_id": f"{icao24}_{first_seen or end}",
                            "icao24": str(icao24).upper(),
                            "callsign": (flight.get("callsign") or "").strip(),
                            "departure_icao": departure,
                            "arrival_icao": arrival,
                            "actual_departure": (
                                datetime.fromtimestamp(first_seen, tz=UTC).isoformat()
                                if first_seen
                                else None
                            ),
                            "actual_arrival": (
                                datetime.fromtimestamp(last_seen, tz=UTC).isoformat()
                                if last_seen
                                else None
                            ),
                            "status": "landed" if arrival else "en-route",
                            "delay_minutes": 0,
                            "source": "opensky",
                            "collected_at": collected_at,
                        }
                    )
            return out

        workers = min(self.settings.flights_max_workers, len(airports))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(fetch_airport, airports):
                rows.extend(result)
        return rows
