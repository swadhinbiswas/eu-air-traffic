"""Stateless geometric route-leg estimation.

adsb.lol's live feed carries no route, so for each position we estimate at most
one leg from geometry alone: an aircraft low/climbing near airport A is
probably departing A; low/descending near B is probably arriving at B. Cruise
traffic yields nothing here — the :mod:`services.route_memory` layer fills
those legs from previously observed callsign routes.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from services.sources.airports import europe_airports

# Only terminal-area traffic gets an estimate: below this altitude (ft) or
# climbing/descending fast, within RADIUS_KM of an airport.
ESTIMATE_ALT_FT = 12_000.0
ESTIMATE_RATE_FPM = 500.0
RADIUS_KM = 40.0
_GROUND_ALT_FT = 3_000.0
_GROUND_RADIUS_KM = 15.0


@lru_cache(maxsize=1)
def _airport_points() -> list[tuple[str, float, float]]:
    """All EU airports as (icao, lat, lon)."""
    points: list[tuple[str, float, float]] = []
    for airport in europe_airports():
        icao = airport.get("icao")
        lat = airport.get("latitude")
        lon = airport.get("longitude")
        if not icao or lat is None or lon is None:
            continue
        try:
            points.append((str(icao).upper(), float(lat), float(lon)))
        except (TypeError, ValueError):
            continue
    return points


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = rlat2 - rlat1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _nearest_airport(lat: float, lon: float, radius_km: float) -> str | None:
    """Nearest airport ICAO within radius, with a bounding-box prefilter."""
    best: str | None = None
    best_dist = radius_km
    # ~0.5° ≈ 55 km; cheap reject before the haversine.
    for icao, alat, alon in _airport_points():
        if abs(alat - lat) > 0.5 or abs(alon - lon) > 0.5:
            continue
        dist = _haversine_km(lat, lon, alat, alon)
        if dist < best_dist:
            best, best_dist = icao, dist
    return best


def _num(value: Any) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def estimate_leg(row: dict[str, Any]) -> dict[str, Any]:
    """Estimate ``(origin, destination, phase)`` for one position row.

    Returns at most one leg: ``origin`` when climbing/on ground near an
    airport, ``destination`` when descending near one, else ``(None, None)``.
    Phase is one of ``departing`` / ``arriving`` / ``ground`` / ``enroute``.
    """
    lat = _num(row.get("latitude"))
    lon = _num(row.get("longitude"))
    if lat is None or lon is None:
        return {"origin": None, "destination": None, "phase": "enroute"}

    alt = _num(row.get("altitude"))
    rate = _num(row.get("vertical_rate"))
    on_ground = bool(row.get("on_ground"))

    if on_ground:
        airport = _nearest_airport(lat, lon, _GROUND_RADIUS_KM)
        return {"origin": airport, "destination": None, "phase": "ground" if airport else "enroute"}

    low = alt is not None and alt < ESTIMATE_ALT_FT
    climbing = rate is not None and rate > ESTIMATE_RATE_FPM
    descending = rate is not None and rate < -ESTIMATE_RATE_FPM
    if not (low or climbing or descending):
        return {"origin": None, "destination": None, "phase": "enroute"}

    airport = _nearest_airport(lat, lon, RADIUS_KM)
    if airport is None:
        return {"origin": None, "destination": None, "phase": "enroute"}
    if descending and not climbing:
        return {"origin": None, "destination": airport, "phase": "arriving"}
    return {"origin": airport, "destination": None, "phase": "departing"}
