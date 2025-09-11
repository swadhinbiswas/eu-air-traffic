"""Aviation weather — METAR and TAF from aviationweather.gov.

METAR (with flight category VFR/MVFR/IFR/LIFR) is fetched for the whole European
bounding box; TAF is fetched for the busiest hubs. Emitted in the same weather
schema the Silver transform expects, so batch and live stay consistent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from config.logging import logger
from services.sources.base import Source

AWC = "https://aviationweather.gov/api/data"
EU_BBOX = "35,-12,62,32"

HEADERS = {
    "User-Agent": "eu-air-traffic-collector/2.0 (+https://github.com/swadhinbiswas/air-traffic)",
    "Accept": "application/json",
}

# TAF is only available for larger fields; keep it to the main hubs.
TAF_AIRPORTS = [
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
]


class _AviationWeatherSource(Source):
    def __init__(self, app_settings=None, session: requests.Session | None = None) -> None:
        super().__init__(app_settings)
        self._session = session or requests.Session()

    def _get_json(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        try:
            res = self._session.get(
                f"{AWC}/{path}",
                params=params,
                headers=HEADERS,
                timeout=self.settings.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("[weather] %s: %s", path, exc)
            return []
        if res.status_code != 200:
            logger.warning("[weather] %s -> HTTP %s", path, res.status_code)
            return []
        try:
            payload = res.json()
        except ValueError:
            return []
        return payload if isinstance(payload, list) else []


class MetarSource(_AviationWeatherSource):
    """METAR observations for the European bbox."""

    name = "metar"
    key = "station_icao"

    def fetch(self) -> list[dict[str, Any]]:
        rows = self._get_json("metar", {"bbox": EU_BBOX, "format": "json"})
        collected_at = datetime.now(UTC).isoformat()
        kt_to_ms = 0.514444
        out: list[dict[str, Any]] = []
        for m in rows:
            if not m.get("icaoId") or m.get("lat") is None:
                continue
            observed = m.get("reportTime") or m.get("obsTime")
            wind_kt = m.get("wspd")
            vis_raw = m.get("visib")
            vis_m: int | None = None
            if vis_raw is not None:
                try:
                    vis_m = round(float(str(vis_raw).replace("+", "").strip()) * 1609.344)
                except ValueError:
                    vis_m = None
            out.append(
                {
                    "_kind": "metar",
                    "station_icao": m["icaoId"],
                    "timestamp": observed,
                    "temperature_c": m.get("temp"),
                    "humidity_pct": None,
                    "wind_speed_ms": (
                        round(float(wind_kt) * kt_to_ms, 2) if wind_kt is not None else None
                    ),
                    "visibility_m": vis_m,
                    "condition": m.get("fltCat") or m.get("cover"),
                    "pressure_hpa": m.get("altim"),
                    "source": "aviationweather",
                    "collected_at": collected_at,
                    "ingestion_date": datetime.now(UTC).strftime("%Y-%m-%d"),
                    "name": m.get("name"),
                    "latitude": m.get("lat"),
                    "longitude": m.get("lon"),
                    "dewpoint_c": m.get("dewp"),
                    "wind_dir_deg": m.get("wdir"),
                    "wind_speed_kt": wind_kt,
                    "gust_kt": m.get("wgst"),
                    "visibility_raw": vis_raw,
                    "flight_category": m.get("fltCat"),
                    "raw_metar": m.get("rawOb"),
                }
            )
        return out


class TafSource(_AviationWeatherSource):
    """Terminal aerodrome forecasts for the major hubs."""

    name = "taf"
    key = "station_icao"

    def fetch(self) -> list[dict[str, Any]]:
        rows = self._get_json("taf", {"ids": ",".join(TAF_AIRPORTS), "format": "json"})
        collected_at = datetime.now(UTC).isoformat()
        return [
            {
                "_kind": "taf",
                "station_icao": t.get("icaoId"),
                "issue_time": t.get("issueTime"),
                "valid_from": t.get("validTimeFrom"),
                "valid_to": t.get("validTimeTo"),
                "raw_taf": t.get("rawTAF"),
                "source": "aviationweather",
                "collected_at": collected_at,
            }
            for t in rows
            if t.get("icaoId")
        ]
