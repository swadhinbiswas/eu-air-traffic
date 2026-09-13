"""Weather forecasts from Open-Meteo (keyless).

One batched request covers up to 50 airports, returning current conditions plus
the forward 24h hourly window in the shared weather schema.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from config.logging import logger
from services.sources.airports import airport_coordinates
from services.sources.base import Source

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
BATCH_SIZE = 50
FORECAST_HOURS = 24

WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def weather_label(code: Any) -> str:
    try:
        return WMO_CODES.get(int(code), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


def _at(values: list[Any] | None, index: int) -> Any:
    if not values or index >= len(values):
        return None
    return values[index]


class ForecastSource(Source):
    """Current + 24h hourly forecast for European airports."""

    name = "forecast"
    # A station has ~25 rows (current + hourly); station_icao alone collapsed
    # them all into one. Key on station + timestamp.
    key = "forecast_key"

    def __init__(self, app_settings=None, session: requests.Session | None = None) -> None:
        super().__init__(app_settings)
        self._session = session or requests.Session()

    def _fetch_batch(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "latitude": ",".join(f"{a['latitude_deg']:.4f}" for a in batch),
            "longitude": ",".join(f"{a['longitude_deg']:.4f}" for a in batch),
            "current": "temperature_2m,relative_humidity_2m,precipitation,"
            "wind_speed_10m,wind_direction_10m,weather_code",
            "hourly": "temperature_2m,precipitation,wind_speed_10m,wind_direction_10m,weather_code",
            "forecast_days": 2,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }
        try:
            res = self._session.get(
                OPEN_METEO, params=params, timeout=self.settings.request_timeout_seconds
            )
            res.raise_for_status()
            payload = res.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("[forecast] open-meteo batch failed: %s", exc)
            return []
        return payload if isinstance(payload, list) else [payload]

    def _rows_for(self, icao: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        collected_at = datetime.now(UTC).isoformat()
        rows: list[dict[str, Any]] = []
        current = payload.get("current") or {}
        if current:
            code = current.get("weather_code")
            rows.append(
                {
                    "_kind": "forecast",
                    "station_icao": icao,
                    "forecast_key": f"{icao}_current_{current.get('time', '')}",
                    "timestamp": f"{current.get('time', '')}:00+00:00",
                    "is_forecast": False,
                    "temperature_c": current.get("temperature_2m"),
                    "humidity_pct": current.get("relative_humidity_2m"),
                    "precipitation_mm": current.get("precipitation"),
                    "wind_speed_ms": current.get("wind_speed_10m"),
                    "wind_direction_deg": current.get("wind_direction_10m"),
                    "weather_code": code,
                    "condition": weather_label(code),
                    "source": "open-meteo",
                    "collected_at": collected_at,
                }
            )
        hourly = payload.get("hourly") or {}
        now = datetime.now(UTC)
        for index, stamp in enumerate(hourly.get("time") or []):
            try:
                when = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
            except ValueError:
                continue
            if when < now - timedelta(hours=1) or when > now + timedelta(hours=FORECAST_HOURS):
                continue
            code = _at(hourly.get("weather_code"), index)
            rows.append(
                {
                    "_kind": "forecast",
                    "station_icao": icao,
                    "forecast_key": f"{icao}_hourly_{stamp}",
                    "timestamp": f"{stamp}:00+00:00",
                    "is_forecast": True,
                    "temperature_c": _at(hourly.get("temperature_2m"), index),
                    "humidity_pct": None,
                    "precipitation_mm": _at(hourly.get("precipitation"), index),
                    "wind_speed_ms": _at(hourly.get("wind_speed_10m"), index),
                    "wind_direction_deg": _at(hourly.get("wind_direction_10m"), index),
                    "weather_code": code,
                    "condition": weather_label(code),
                    "source": "open-meteo",
                    "collected_at": collected_at,
                }
            )
        return rows

    def fetch(self) -> list[dict[str, Any]]:
        airports = airport_coordinates(limit=400)
        if not airports:
            return []
        rows: list[dict[str, Any]] = []
        for start in range(0, len(airports), BATCH_SIZE):
            batch = airports[start : start + BATCH_SIZE]
            payloads = self._fetch_batch(batch)
            for airport, payload in zip(batch, payloads, strict=False):
                rows.extend(self._rows_for(airport["icao"], payload))
        return rows
