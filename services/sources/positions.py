"""Live aircraft positions — adsb.lol with an OpenSky fallback.

Community ADS-B APIs block Cloudflare egress but are reachable from the VPS, so
this is the primary live feed. The canonical row keeps full telemetry (speed,
Mach, IAS/TAS, wind aloft, squawk) that the dashboard renders and the sink
persists.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import requests

from config.logging import logger
from services.sources.base import Source

ADSB_LOL = "https://api.adsb.lol/v2"
OPENSKY = "https://opensky-network.org/api"

HEADERS = {
    "User-Agent": "eu-air-traffic-collector/2.0 (+https://github.com/swadhinbiswas/air-traffic)",
    "Accept": "application/json",
}

# EU coverage circles: (lat, lon, radius_nm). adsb.lol accepts at most 250nm.
EU_CENTERS: list[tuple[float, float, int]] = [
    (50.0, 10.0, 250),
    (52.5, 13.4, 250),
    (51.5, -0.1, 250),
    (48.9, 2.3, 250),
    (40.4, -3.7, 250),
    (41.9, 12.5, 250),
    (52.3, 4.9, 250),
    (47.4, 19.0, 250),
    (59.3, 18.0, 250),
    (44.4, 26.1, 250),
]

OPENSKY_BBOX = {"lamin": "35", "lomin": "-12", "lamax": "62", "lomax": "32"}


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # reject NaN


def normalise_adsb(ac: dict[str, Any]) -> dict[str, Any] | None:
    """Map a tar1090-style aircraft record to the canonical position row."""
    hex_code = str(ac.get("hex") or "").upper()
    lat, lon = _num(ac.get("lat")), _num(ac.get("lon"))
    if not hex_code or lat is None or lon is None:
        return None
    return {
        "icao24": hex_code,
        "callsign": (ac.get("flight") or "").strip(),
        "registration": ac.get("r"),
        "aircraft_type": ac.get("t"),
        "latitude": lat,
        "longitude": lon,
        "altitude": _num(ac.get("alt_baro")) or _num(ac.get("alt_geom")),
        "altitude_geom": _num(ac.get("alt_geom")),
        "velocity": _num(ac.get("gs")),
        "heading": _num(ac.get("track")) or _num(ac.get("true_heading")),
        "true_heading": _num(ac.get("true_heading")),
        "mag_heading": _num(ac.get("mag_heading")),
        "roll": _num(ac.get("roll")),
        "vertical_rate": _num(ac.get("baro_rate")),
        "geom_rate": _num(ac.get("geom_rate")),
        "mach": _num(ac.get("mach")),
        "ias": _num(ac.get("ias")),
        "tas": _num(ac.get("tas")),
        "oat": _num(ac.get("oat")),
        "tat": _num(ac.get("tat")),
        "wind_dir": _num(ac.get("wd")),
        "wind_speed": _num(ac.get("ws")),
        "nav_qnh": _num(ac.get("nav_qnh")),
        "nav_altitude": _num(ac.get("nav_altitude_mcp")),
        "nav_heading": _num(ac.get("nav_heading")),
        "route": ac.get("route"),
        "squawk": ac.get("squawk"),
        "emergency": ac.get("emergency"),
        "category": ac.get("category"),
        "db_flags": ac.get("dbFlags"),
        "nic": ac.get("nic"),
        "nac_p": ac.get("nac_p"),
        "nac_v": ac.get("nac_v"),
        "sil": ac.get("sil"),
        "rc": _num(ac.get("rc")),
        "on_ground": bool(ac.get("ground")),
        "source": "adsb.lol",
        "collected_at": datetime.now(UTC).isoformat(),
    }


def normalise_opensky(state: list[Any]) -> dict[str, Any] | None:
    """Map an OpenSky state vector to the canonical position row."""
    if not state or state[5] is None or state[6] is None:
        return None
    alt_m = state[7] if state[7] is not None else state[13]
    velocity_ms = _num(state[9])
    baro_ms = _num(state[11])
    return {
        "icao24": str(state[0] or "").upper(),
        "callsign": (state[1] or "").strip(),
        "latitude": state[6],
        "longitude": state[5],
        "altitude": round(alt_m / 0.3048) if alt_m is not None else None,
        "altitude_geom": None,
        "velocity": round(velocity_ms * 1.94384) if velocity_ms is not None else None,
        "heading": _num(state[10]),
        "vertical_rate": round(baro_ms * 196.85) if baro_ms is not None else None,
        "squawk": state[14],
        "on_ground": bool(state[8]),
        "source": "opensky",
        "collected_at": datetime.now(UTC).isoformat(),
    }


class PositionsSource(Source):
    """Live aircraft across European coverage circles."""

    name = "positions"
    key = "icao24"

    def __init__(self, app_settings=None, session: requests.Session | None = None) -> None:
        super().__init__(app_settings)
        self._session = session or requests.Session()
        # Circles cooling down after a 429, mapped to a monotonic timestamp.
        self._cooldown_until: dict[tuple[float, float, int], float] = {}

    @staticmethod
    def _retry_after_seconds(res: requests.Response) -> float:
        try:
            return max(float(res.headers.get("Retry-After", "")), 30.0)
        except (TypeError, ValueError):
            return 120.0

    def _fetch_adsb(self) -> tuple[dict[str, dict[str, Any]], int]:
        """Fetch all coverage circles concurrently and dedupe by ICAO24.

        Serial requests took 10 round-trips every tick; parallelising keeps the
        15s cadence comfortably within the free-tier budget.

        Returns ``(rows by icao24, failed circle count)``. A 429 parks that
        circle in cooldown instead of hammering the rate limiter every tick.
        """

        from concurrent.futures import ThreadPoolExecutor

        by_hex: dict[str, dict[str, Any]] = {}

        def fetch_center(center: tuple[float, float, int]) -> tuple[list[dict[str, Any]], bool]:
            lat, lon, dist = center
            if time.monotonic() < self._cooldown_until.get(center, 0.0):
                return [], True
            try:
                res = self._session.get(
                    f"{ADSB_LOL}/lat/{lat:.2f}/lon/{lon:.2f}/dist/{dist}",
                    headers=HEADERS,
                    timeout=self.settings.request_timeout_seconds,
                )
            except (requests.RequestException, ValueError) as exc:
                logger.warning("[positions] adsb.lol %s,%s unreachable: %s", lat, lon, exc)
                return [], True
            if res.status_code == 429:
                wait = self._retry_after_seconds(res)
                self._cooldown_until[center] = time.monotonic() + wait
                logger.warning(
                    "[positions] adsb.lol %s,%s rate-limited (429) — cooling down %ds",
                    lat,
                    lon,
                    int(wait),
                )
                return [], True
            if res.status_code != 200:
                logger.warning("[positions] adsb.lol %s,%s HTTP %s", lat, lon, res.status_code)
                return [], True
            try:
                rows = [normalise_adsb(ac) for ac in res.json().get("ac", [])]
            except ValueError as exc:
                logger.warning("[positions] adsb.lol %s,%s bad payload: %s", lat, lon, exc)
                return [], True
            return [row for row in rows if row], False

        workers = min(self.settings.adsb_max_workers, len(EU_CENTERS))
        failed = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for rows, circle_failed in pool.map(fetch_center, EU_CENTERS):
                failed += circle_failed
                for row in rows:
                    by_hex[row["icao24"]] = row
        return by_hex, failed

    def _fetch_opensky(self) -> list[dict[str, Any]]:
        auth = None
        if self.settings.opensky_username and self.settings.opensky_password:
            auth = (self.settings.opensky_username, self.settings.opensky_password)
        try:
            res = self._session.get(
                f"{OPENSKY}/states/all",
                params=OPENSKY_BBOX,
                headers=HEADERS,
                auth=auth,
                timeout=self.settings.request_timeout_seconds,
            )
            if res.status_code == 200:
                rows = [normalise_opensky(s) for s in (res.json().get("states") or [])]
                return [r for r in rows if r]
        except (requests.RequestException, ValueError) as exc:
            logger.debug("[positions] opensky: %s", exc)
        return []

    def fetch(self) -> list[dict[str, Any]]:
        by_hex, failed = self._fetch_adsb()
        adsb_n = sum(1 for row in by_hex.values() if row.get("source") == "adsb.lol")
        if failed:
            # Degraded tick: fill the gaps from OpenSky, adsb.lol rows win.
            for row in self._fetch_opensky():
                by_hex.setdefault(row["icao24"], row)
            logger.warning(
                "[positions] %d/%d adsb.lol circles failed — merged %d OpenSky rows (%d adsb.lol)",
                failed,
                len(EU_CENTERS),
                len(by_hex) - adsb_n,
                adsb_n,
            )
        if not by_hex:
            logger.warning("[positions] no live aircraft from adsb.lol or OpenSky")
            return []
        logger.info(
            "[positions] %d aircraft (%d adsb.lol, %d opensky)",
            len(by_hex),
            adsb_n,
            len(by_hex) - adsb_n,
        )
        return list(by_hex.values())
