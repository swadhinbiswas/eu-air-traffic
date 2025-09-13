"""Flight movements from OpenSky Network.

OpenSky's current credit model (REST docs): every ``/flights/*`` request costs
**4 credits** from a dedicated 4,000/day budget, and authentication is the
OAuth2 client-credentials flow only — basic auth is no longer accepted.

The strategy therefore spends as few requests as possible:

* ``/flights/all`` — one request per cycle for every movement that departed and
  arrived inside the window (max 2 h). Cheapest way to get both ends, and the
  main feed for delay/route analytics.
* ``/flights/departure`` for the busiest hubs — live departures, so en-route
  flights reach the lake before they land.
* ``/flights/arrival`` — arrivals are published in a **nightly batch**, so a
  recent window returns nothing. Fetched every few hours for the previous day
  instead of on every cycle.

With the defaults (12 hubs, 30-minute cycle) this is roughly 2,700 credits/day.
"""

from __future__ import annotations

import time
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

# Busiest EU/EEA + near-neighbour hubs, polled for live departures.
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
    """Completed movements plus live departures for the major European hubs."""

    name = "flights"
    key = "flight_id"

    def __init__(self, app_settings=None, session: requests.Session | None = None) -> None:
        super().__init__(app_settings)
        self._session = session or requests.Session()
        # "Never" must mean due now: monotonic() can be far below the interval
        # on a freshly booted host (CI runners, a just-restarted collector).
        self._last_arrival_backfill = float("-inf")

    @property
    def _auth_kwargs(self) -> dict[str, Any]:
        from services.opensky_auth import auth_for

        extra_headers, auth = auth_for(self.settings)
        return {"headers": {**HEADERS, **extra_headers}, "auth": auth}

    def _get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            res = self._session.get(
                f"{OPENSKY}{path}",
                params=params,
                timeout=self.settings.request_timeout_seconds,
                **self._auth_kwargs,
            )
        except requests.RequestException as exc:
            logger.debug("[flights] opensky %s: %s", path, exc)
            return []
        if res.status_code == 404:  # documented: no flights in the window
            return []
        if res.status_code == 429:
            logger.warning(
                "[flights] OpenSky rate limit reached (retry in %ss) — credits exhausted",
                res.headers.get("X-Rate-Limit-Retry-After-Seconds", "?"),
            )
            return []
        if res.status_code != 200:
            logger.debug("[flights] opensky %s -> HTTP %s", path, res.status_code)
            return []
        try:
            payload = res.json()
        except ValueError:
            return []
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _row(
        flight: dict[str, Any],
        collected_at: str,
        kind: str = "",
        airport: str = "",
    ) -> dict[str, Any] | None:
        icao24 = flight.get("icao24")
        if not icao24:
            return None
        first_seen = flight.get("firstSeen")
        last_seen = flight.get("lastSeen")
        departure = flight.get("estDepartureAirport")
        arrival = flight.get("estArrivalAirport")
        # The queried airport is authoritative for its own direction even when
        # OpenSky omits the estimated field.
        if kind == "departure" and not departure:
            departure = airport
        if kind == "arrival" and not arrival:
            arrival = airport
        return {
            "flight_id": f"{icao24}_{first_seen or last_seen or 0}",
            "icao24": str(icao24).upper(),
            "callsign": (flight.get("callsign") or "").strip(),
            "departure_icao": departure,
            "arrival_icao": arrival,
            "actual_departure": (
                datetime.fromtimestamp(first_seen, tz=UTC).isoformat() if first_seen else None
            ),
            "actual_arrival": (
                datetime.fromtimestamp(last_seen, tz=UTC).isoformat() if last_seen else None
            ),
            "status": "landed" if arrival else "en-route",
            # OpenSky has no schedule, so a delay cannot be known. NULL keeps
            # these rows out of punctuality averages instead of counting them
            # as perfectly on time (the old 0 made OTP trend to 100%).
            "delay_minutes": None,
            "source": "opensky",
            "collected_at": collected_at,
        }

    def fetch(self) -> list[dict[str, Any]]:
        if not (self.settings.opensky_client_id or self.settings.opensky_username):
            logger.debug("[flights] OpenSky credentials unavailable — skipping")
            return []

        now = datetime.now(UTC)
        end = int(now.timestamp())
        lookback = min(self.settings.flights_lookback_minutes, 120)
        begin = int((now - timedelta(minutes=lookback)).timestamp())
        collected_at = now.isoformat()
        airports = TOP_AIRPORTS[: self.settings.flights_airports]
        rows: list[dict[str, Any]] = []

        # 1. Network-wide completed movements: one request, both ends.
        for flight in self._get("/flights/all", {"begin": begin, "end": end}):
            row = self._row(flight, collected_at)
            if row:
                rows.append(row)

        from concurrent.futures import ThreadPoolExecutor

        workers = min(self.settings.flights_max_workers, len(airports), 8)

        # 2. Live departures for the hubs.
        def departures(icao: str) -> list[dict[str, Any]]:
            out = []
            params = {"airport": icao, "begin": begin, "end": end}
            for flight in self._get("/flights/departure", params):
                row = self._row(flight, collected_at, kind="departure", airport=icao)
                if row:
                    out.append(row)
            return out

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(departures, airports):
                rows.extend(result)

        # 3. Arrival backfill — OpenSky only publishes arrivals in a nightly
        # batch, so ask for the previous day, not for the last few minutes.
        if (
            time.monotonic() - self._last_arrival_backfill
            >= self.settings.flights_arrival_interval_seconds
        ):
            self._last_arrival_backfill = time.monotonic()
            backfill_end = end - 24 * 3600
            backfill_begin = backfill_end - 24 * 3600

            def arrivals(icao: str) -> list[dict[str, Any]]:
                out = []
                params = {"airport": icao, "begin": backfill_begin, "end": backfill_end}
                for flight in self._get("/flights/arrival", params):
                    row = self._row(flight, collected_at, kind="arrival", airport=icao)
                    if row:
                        out.append(row)
                return out

            with ThreadPoolExecutor(max_workers=workers) as pool:
                for result in pool.map(arrivals, airports):
                    rows.extend(result)

        logger.debug("[flights] %d movements from OpenSky", len(rows))
        return rows
