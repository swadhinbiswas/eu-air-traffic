"""Flight data collection from the OpenSky Network public REST API.

OpenSky exposes historical flight tracks free of charge for anonymous use,
with higher limits for authenticated users. This collector:

1. Picks the busiest European airports (by capability score) to query.
2. Queries departure + arrival flights within the incremental window.
3. Normalises the raw response into a stable Bronze schema.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings
from ingestion.base import Collector, IngestionError
from ingestion.utils import retry

OPENSKY_BASE_URL = "https://opensky-network.org/api"


class FlightCollector(Collector):
    """Collects flight movements for European airports."""

    name = "flights"
    source = "flights"

    def __init__(self, app_settings: Settings | None = None, top_airports: int = 20) -> None:
        super().__init__(app_settings)
        self.top_airports = top_airports

    # ── HTTP helpers ──────────────────────────────────────────────────────
    @property
    def _auth(self) -> tuple[str, str] | None:
        if self.settings.opensky_username and self.settings.opensky_password:
            return (self.settings.opensky_username, self.settings.opensky_password)
        return None

    @retry()
    def _get_airport_flights(
        self, icao: str, begin: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        url = f"{OPENSKY_BASE_URL}/flights/airport"
        params: dict[str, str | int] = {
            "airport": icao,
            "begin": int(begin.timestamp()),
            "end": int(end.timestamp()),
        }
        response = requests.get(
            url,
            params=params,
            auth=self._auth,
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    # ── Airports to monitor ───────────────────────────────────────────────
    def _target_airports(self) -> list[str]:
        silver = self.settings.silver_dir / "airports" / "airports.parquet"
        icaos: list[str] = []
        try:
            import polars as pl

            df = pl.read_parquet(silver)
            icaos = (
                df.filter(pl.col("type") == "large_airport")
                .sort("score", descending=True)
                .head(self.top_airports)["ident"]
                .to_list()
            )
        except Exception:  # noqa: BLE001 - silver not built yet → fall back to a known set
            icaos = ["EDDF", "EGLL", "LFPG", "EHAM", "EDDM", "LEMD", "LEBL", "LIRF", "LOWW", "LSZH"]
        return icaos or ["EDDF", "EGLL", "LFPG"]

    # ── Normalisation ─────────────────────────────────────────────────────
    @staticmethod
    def _normalise(raw: dict[str, Any], airport: str, direction: str) -> dict[str, Any]:
        return {
            "icao24": raw.get("icao24"),
            "callsign": raw.get("callsign"),
            "flight_direction": direction,
            "queried_airport": airport,
            "est_departure_airport": raw.get("estDepartureAirport"),
            "est_arrival_airport": raw.get("estArrivalAirport"),
            "first_seen": raw.get("firstSeen"),
            "last_seen": raw.get("lastSeen"),
            "est_departure_airport_horiz_distance": raw.get("estDepartureAirportHorizDistance"),
            "est_departure_airport_vert_distance": raw.get("estDepartureAirportVertDistance"),
            "est_arrival_airport_horiz_distance": raw.get("estArrivalAirportHorizDistance"),
            "est_arrival_airport_vert_distance": raw.get("estArrivalAirportVertDistance"),
            "departure_airport_candidates_count": raw.get("departureAirportCandidatesCount"),
            "arrival_airport_candidates_count": raw.get("arrivalAirportCandidatesCount"),
            "source": "opensky",
            "collected_at": datetime.now(UTC).isoformat(),
        }

    # ── Collector interface ───────────────────────────────────────────────
    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        if self.settings.mock_mode:
            return self._mock([])

        if not self._auth:
            logger.warning(
                "[flights] OpenSky credentials missing and MOCK_MODE=false — returning no records"
            )
            return []

        end = datetime.now(UTC)
        if (end - start) > timedelta(hours=24):
            logger.warning("[flights] window >24h; OpenSky limits anonymous queries — clamping")
            start = end - timedelta(hours=24)

        airports = self._target_airports()
        records: list[dict[str, Any]] = []
        failures = 0

        for index, icao in enumerate(airports, start=1):
            try:
                flights = self._get_airport_flights(icao, start, end)
                for direction, subset in (
                    ("arrival", [f for f in flights if f.get("estArrivalAirport") == icao]),
                    ("departure", [f for f in flights if f.get("estDepartureAirport") == icao]),
                ):
                    records.extend(self._normalise(flight, icao, direction) for flight in subset)
                logger.info(
                    "[flights] %s/%s %s → %s flights", index, len(airports), icao, len(flights)
                )
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.error("[flights] failed for %s: %s", icao, exc)
            self._throttle()

        if failures == len(airports) and not self.settings.mock_mode:
            raise IngestionError("All OpenSky queries failed")

        return records


if __name__ == "__main__":
    collector = FlightCollector()
    count = collector.run()
    logger.info("Collected %s flight records", count)
