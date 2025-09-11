"""Open-Meteo weather forecast collection (free, no API key).

Open-Meteo exposes a keyless forecast API that accepts **batched coordinates**
(comma-separated latitude/longitude lists), so we can pull current conditions
plus a 24-hour hourly forecast for many European airports in a handful of
requests. Records are emitted in long format — one row per airport per
timestamp — and carry an ``is_forecast`` flag distinguishing observations from
forward-looking hours.

Reference: https://open-meteo.com/en/docs
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from config.logging import logger
from config.settings import Settings, settings
from ingestion.base import Collector
from ingestion.utils import retry

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes → human-readable condition.
# https://open-meteo.com/en/docs#weathervariables
WMO_CODES: dict[int, str] = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def weather_label(code: int | None) -> str:
    """Map a WMO code to a short condition label."""
    if code is None:
        return "Unknown"
    return WMO_CODES.get(int(code), "Unknown")


class OpenMeteoCollector(Collector):
    """Collects current + hourly forecast weather for European airports."""

    name = "weather_forecast"
    source = "weather_forecast"

    BATCH_SIZE = 50
    FORECAST_HOURS = 24

    def __init__(self, app_settings: Settings | None = None, max_airports: int = 400) -> None:
        super().__init__(app_settings)
        self.max_airports = max_airports

    # ── coordinates ────────────────────────────────────────────────────────
    def _airport_coordinates(self) -> list[dict[str, Any]]:
        """Read lat/lon from the silver airports table (EU reference dataset)."""
        silver = self.settings.silver_dir / "airports" / "airports.parquet"
        try:
            import polars as pl

            df = pl.read_parquet(silver).filter(
                pl.col("latitude_deg").is_not_null() & pl.col("longitude_deg").is_not_null()
            )
            if df.height:
                return df.head(self.max_airports).to_dicts()
        except Exception:  # noqa: BLE001 - silver missing on first run
            logger.debug("[weather_forecast] silver airports unavailable", exc_info=True)
        return [
            {"ident": icao, "latitude_deg": lat, "longitude_deg": lon}
            for icao, lat, lon in (
                ("EDDF", 50.03, 8.56),
                ("EGLL", 51.47, -0.45),
                ("LFPG", 49.01, 2.55),
            )
        ]

    # ── fetching ───────────────────────────────────────────────────────────
    @staticmethod
    @retry()
    def _fetch_batch(lats: list[float], lons: list[float]) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "latitude": ",".join(f"{v:.4f}" for v in lats),
            "longitude": ",".join(f"{v:.4f}" for v in lons),
            "current": "temperature_2m,relative_humidity_2m,precipitation,"
            "wind_speed_10m,wind_direction_10m,weather_code",
            "hourly": "temperature_2m,precipitation,wind_speed_10m,wind_direction_10m,weather_code",
            "forecast_days": 2,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }
        response = requests.get(
            OPEN_METEO_URL, params=params, timeout=settings.request_timeout_seconds
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else [payload]

    def _rows_for(self, ident: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        collected_at = datetime.now(UTC).isoformat()
        units = payload.get("current_units") or {}
        rows: list[dict[str, Any]] = []

        current = payload.get("current") or {}
        if current:
            code = current.get("weather_code")
            rows.append(
                {
                    "station_icao": ident,
                    "timestamp": f"{current.get('time', '')}:00+00:00",
                    "is_forecast": False,
                    "temperature_c": current.get("temperature_2m"),
                    "humidity_pct": current.get("relative_humidity_2m"),
                    "precipitation_mm": current.get("precipitation"),
                    "wind_speed_ms": current.get("wind_speed_10m"),
                    "wind_direction_deg": current.get("wind_direction_10m"),
                    "weather_code": code,
                    "condition": weather_label(code),
                    "wind_unit": units.get("wind_speed_10m", "m/s"),
                    "source": "open-meteo",
                    "collected_at": collected_at,
                }
            )

        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        now = datetime.now(UTC)
        for index, stamp in enumerate(times):
            try:
                when = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
            except ValueError:
                continue
            # Keep the forward-looking window only.
            if when < now - timedelta(hours=1):
                continue
            if when > now + timedelta(hours=self.FORECAST_HOURS):
                continue
            code = _at(hourly.get("weather_code"), index)
            rows.append(
                {
                    "station_icao": ident,
                    "timestamp": stamp + ":00+00:00",
                    "is_forecast": True,
                    "temperature_c": _at(hourly.get("temperature_2m"), index),
                    "humidity_pct": None,
                    "precipitation_mm": _at(hourly.get("precipitation"), index),
                    "wind_speed_ms": _at(hourly.get("wind_speed_10m"), index),
                    "wind_direction_deg": _at(hourly.get("wind_direction_10m"), index),
                    "weather_code": code,
                    "condition": weather_label(code),
                    "wind_unit": "m/s",
                    "source": "open-meteo",
                    "collected_at": collected_at,
                }
            )
        return rows

    def _synthetic(self, airports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deterministic forecast data for MOCK_MODE / CI without network."""
        import random

        rows: list[dict[str, Any]] = []
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        for airport in airports:
            ident = str(airport.get("ident") or "UNKN")
            rng = random.Random(ident)
            base_temp = 8 + rng.random() * 20
            for offset in range(self.FORECAST_HOURS + 1):
                when = now + timedelta(hours=offset)
                code = rng.choice([0, 1, 2, 3, 45, 61, 63, 80, 95])
                rows.append(
                    {
                        "station_icao": ident,
                        "timestamp": when.isoformat(),
                        "is_forecast": offset > 0,
                        "temperature_c": round(base_temp + 5 * ((offset % 24) / 24) - 2.5, 1),
                        "humidity_pct": round(50 + rng.random() * 40, 0),
                        "precipitation_mm": round(rng.random() * 2, 2),
                        "wind_speed_ms": round(rng.random() * 12, 1),
                        "wind_direction_deg": round(rng.random() * 360, 0),
                        "weather_code": code,
                        "condition": weather_label(code),
                        "wind_unit": "m/s",
                        "source": "open-meteo-synthetic",
                        "collected_at": now.isoformat(),
                    }
                )
        return rows

    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        airports = self._airport_coordinates()
        if self.settings.mock_mode:
            return self._synthetic(airports)

        records: list[dict[str, Any]] = []
        failures = 0
        for offset in range(0, len(airports), self.BATCH_SIZE):
            batch = airports[offset : offset + self.BATCH_SIZE]
            coords = [
                (float(a["latitude_deg"]), float(a["longitude_deg"]))
                for a in batch
                if a.get("latitude_deg") is not None and a.get("longitude_deg") is not None
            ]
            if not coords:
                continue
            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            try:
                payloads = self._fetch_batch(lats, lons)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.error("[weather_forecast] batch at offset %s failed: %s", offset, exc)
                continue
            for airport, payload in zip(batch, payloads, strict=False):
                ident = str(airport.get("ident") or "")
                if ident:
                    records.extend(self._rows_for(ident, payload))

        if failures and not records:
            logger.warning("[weather_forecast] all Open-Meteo batches failed — no records")
        return records


def _at(values: list[Any] | None, index: int) -> Any:
    if not values or index >= len(values):
        return None
    return values[index]


if __name__ == "__main__":
    collector = OpenMeteoCollector()
    count = collector.run()
    logger.info("Collected %s weather_forecast records", count)
