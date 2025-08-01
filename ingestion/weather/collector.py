"""Weather collection from the OpenWeatherMap current-weather API.

For each monitored airport we fetch current conditions (temperature, humidity,
wind, visibility, condition) which we later join to flight delays to study
weather impact. Rate-limited to ``OPENWEATHER_LIMIT`` requests/minute.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings, settings
from ingestion.base import Collector, IngestionError
from ingestion.utils import retry, to_float

OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"


class WeatherCollector(Collector):
    """Collects current weather for a set of airport coordinates."""

    name = "weather"
    source = "weather"

    def __init__(self, app_settings: Settings | None = None, top_airports: int = 30) -> None:
        super().__init__(app_settings)
        self.top_airports = top_airports

    def _airport_coordinates(self) -> list[dict[str, Any]]:
        """Read lat/lon from the silver airports table (falls back to constants)."""
        silver = self.settings.silver_dir / "airports" / "airports.parquet"
        try:
            import polars as pl

            df = pl.read_parquet(silver).filter(
                pl.col("latitude_deg").is_not_null() & pl.col("longitude_deg").is_not_null()
            )
            if df.height:
                return df.head(self.top_airports).to_dicts()
        except Exception:  # noqa: BLE001 - silver missing
            logger.debug("silver airports not ready, using fallback coordinates", exc_info=True)
        return [
            {"ident": a, "latitude_deg": None, "longitude_deg": None}
            for a in ("EDDF", "EGLL", "LFPG")
        ]

    @staticmethod
    @retry()
    def _fetch_weather(lat: float, lon: float) -> dict[str, Any]:
        params = {
            "lat": lat,
            "lon": lon,
            "appid": settings.openweather_api_key,
            "units": "metric",
        }
        response = requests.get(
            OPENWEATHER_URL, params=params, timeout=settings.request_timeout_seconds
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _normalise(raw: dict[str, Any], ident: str) -> dict[str, Any]:
        main = raw.get("main") or {}
        wind = raw.get("wind") or {}
        clouds = raw.get("clouds") or {}
        weather = (raw.get("weather") or [{}])[0]
        return {
            "station_icao": ident,
            "timestamp": datetime.fromtimestamp(raw.get("dt", 0), tz=UTC).isoformat(),
            "temperature_c": to_float(main.get("temp")),
            "feels_like_c": to_float(main.get("feels_like")),
            "humidity_pct": to_float(main.get("humidity")),
            "pressure_hpa": to_float(main.get("pressure")),
            "wind_speed_ms": to_float(wind.get("speed")),
            "wind_direction_deg": to_float(wind.get("deg")),
            "visibility_m": to_float(raw.get("visibility")),
            "clouds_pct": to_float(clouds.get("all")),
            "condition": weather.get("main"),
            "condition_description": weather.get("description"),
            "source": "openweather",
            "collected_at": datetime.now(UTC).isoformat(),
        }

    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        if self.settings.mock_mode:
            return self._mock([])

        if not self.settings.openweather_api_key:
            logger.warning(
                "[weather] OPENWEATHER_API_KEY missing and MOCK_MODE=false — returning no records"
            )
            return []

        airports = self._airport_coordinates()
        records: list[dict[str, Any]] = []
        failures = 0

        for index, airport in enumerate(airports, start=1):
            ident = airport.get("ident") or ""
            lat, lon = airport.get("latitude_deg"), airport.get("longitude_deg")
            if lat is None or lon is None:
                continue
            try:
                raw = self._fetch_weather(float(lat), float(lon))
                records.append(self._normalise(raw, ident))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.error("[weather] failed for %s: %s", ident, exc)
            # Enforce per-minute rate limit from settings.
            time.sleep(60.0 / max(self.settings.openweather_limit, 1))

        if failures == len(airports) and not self.settings.mock_mode:
            raise IngestionError("All OpenWeather queries failed")

        return records


if __name__ == "__main__":
    collector = WeatherCollector()
    count = collector.run()
    logger.info("Collected %s weather records", count)
