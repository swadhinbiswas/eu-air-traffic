"""Airport metadata collection.

Combines the OpenFlights European airport list with AirportDB enrichment
(ICAO-level infrastructure metadata) and a capability score. Falls back to the
on-disk raw cache when no API token is available, then to synthetic data in
mock mode.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings, settings
from ingestion.base import Collector
from ingestion.utils import atomic_write_json, retry

OPENFLIGHTS_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"

EUROPE_ICAO_PREFIXES = settings.europe_icao_prefixes


class AirportCollector(Collector):
    """Collects European airport metadata into the Bronze layer."""

    name = "airports"
    source = "airports"

    def __init__(self, app_settings: Settings | None = None) -> None:
        super().__init__(app_settings)
        self.airports_json = (
            self.settings.project_root / "ingestion" / "airports" / "europe_airports.json"
        )
        self.raw_airport_dir = self.settings.raw_dir / "airport"

    # ── Airport list (OpenFlights) ────────────────────────────────────────
    def european_airports(self) -> list[dict[str, Any]]:
        if self.airports_json.exists():
            return self._load_cached_list()

        logger.info("Downloading OpenFlights airport database…")
        rows = self._fetch_openflights()
        atomic_write_json(self.airports_json, rows)
        return rows

    @staticmethod
    @retry()
    def _fetch_openflights() -> list[dict[str, Any]]:
        response = requests.get(OPENFLIGHTS_URL, timeout=settings.request_timeout_seconds)
        response.raise_for_status()

        airports: list[dict[str, Any]] = []
        import csv

        for row in csv.reader(response.text.splitlines()):
            if len(row) < 14:
                continue
            icao = row[5]
            if not icao or icao == r"\N" or icao[:2] not in EUROPE_ICAO_PREFIXES:
                continue
            airports.append(
                {
                    "icao": icao,
                    "iata": None if row[4] == r"\N" else row[4],
                    "name": row[1],
                    "city": row[2],
                    "country": row[3],
                    "latitude": float(row[6]) if row[6] not in (r"\N", "") else None,
                    "longitude": float(row[7]) if row[7] not in (r"\N", "") else None,
                    "altitude_ft": int(float(row[8])) if row[8] not in (r"\N", "") else None,
                    "airport_type": None if row[12] == r"\N" else row[12],
                }
            )
        airports.sort(key=lambda airport: airport["icao"])
        return airports

    def _load_cached_list(self) -> list[dict[str, Any]]:
        with self.airports_json.open(encoding="utf-8") as handle:
            return json.load(handle)

    # ── Per-airport enrichment (AirportDB) ────────────────────────────────
    def _build_raw_cache(self) -> dict[str, dict[str, Any]]:
        """Index on-disk raw airport JSONs by ICAO for O(1) lookups."""
        cache: dict[str, dict[str, Any]] = {}
        if not self.raw_airport_dir.exists():
            return cache
        for path in self.raw_airport_dir.rglob("*.json"):
            icao = path.stem.upper()
            try:
                cache[icao] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        logger.info("[airports] indexed %s cached raw airport records", len(cache))
        return cache

    def enriched_airport(
        self, airport: dict[str, Any], raw_cache: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        icao = airport.get("icao")
        if not icao:
            return None

        cached = raw_cache.get(icao)
        if cached is not None:
            return self._normalise(airport, cached)

        if self.settings.airportdb_api_token:
            payload = self._fetch_airportdb(icao)
            self._throttle()
            if payload:
                return self._normalise(airport, payload)

        # No token & no cache → keep the light OpenFlights record.
        return airport

    @staticmethod
    @retry()
    def _fetch_airportdb(icao: str) -> dict[str, Any] | None:
        url = f"https://airportdb.io/api/v1/airport/{icao}?apiToken={settings.airportdb_api_token}"
        response = requests.get(url, timeout=settings.request_timeout_seconds)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return None

    @staticmethod
    def _normalise(base: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Merge the rich AirportDB payload with OpenFlights fields + score."""
        merged: dict[str, Any] = {
            **base,
            **{key: value for key, value in payload.items() if value is not None},
        }
        merged["icao"] = merged.get("ident") or base.get("icao")
        merged["score"] = AirportCollector._score(payload)
        merged["collected_at"] = datetime.now(UTC).isoformat()
        return merged

    @staticmethod
    def _score(data: dict[str, Any]) -> int:
        score = 0
        airport_type = data.get("type", "")
        if airport_type == "large_airport":
            score += 100
        elif airport_type == "medium_airport":
            score += 50
        elif airport_type == "small_airport":
            score += 10

        if data.get("scheduled_service") == "yes":
            score += 50

        runways = data.get("runways") or []
        score += len(runways) * 10

        longest_runway = 0
        has_ils = has_hard_surface = has_lights = False
        for runway in runways:
            try:
                length = int(runway.get("length_ft", 0))
                longest_runway = max(longest_runway, length)
            except (ValueError, TypeError):
                pass
            if "le_ils" in runway or "he_ils" in runway:
                has_ils = True
            surface = str(runway.get("surface", "")).upper()
            if "ASP" in surface or "CON" in surface:
                has_hard_surface = True
            if runway.get("lighted") == "1":
                has_lights = True

        score += (longest_runway // 1000) * 5
        if has_ils:
            score += 40
        if has_hard_surface:
            score += 20
        if has_lights:
            score += 20

        score += len(data.get("freqs") or []) * 2
        score += len(data.get("navaids") or []) * 5
        return score

    # ── Collector interface ───────────────────────────────────────────────
    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        airports = self.european_airports()
        if self.settings.mock_mode:
            return self._mock(airports)

        raw_cache = self._build_raw_cache()
        enriched: list[dict[str, Any]] = []
        for index, airport in enumerate(airports, start=1):
            record = self.enriched_airport(airport, raw_cache)
            if record:
                enriched.append(record)
            if index % 100 == 0:
                logger.info("[airports] enriched %s/%s", index, len(airports))
        return enriched


if __name__ == "__main__":
    collector = AirportCollector()
    count = collector.run()
    logger.info("Collected %s airport records", count)
