"""Local reference data — near-static datasets shipped with the repo.

Airports, routes, the fleet and airlines change on the order of months, so the
collectors must not download them on every run. ``scripts.build_reference_data``
materialises them once under ``services/data/`` and this module reads them.

Each helper returns ``None`` when the file is absent so the caller can fall back
to a live fetch (and the build script can regenerate it).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

# Reference files are written by scripts/build_reference_data.py into
# services/data; the path here previously pointed at ingestion/data, so in
# production every reference resolved to None and the live fetch published
# synthetic fallback rows instead.
DATA_DIR = Path(__file__).resolve().parent.parent / "services" / "data"
# Bundled OurAirports list (1658 EU airports with IATA + coordinates).
AIRPORTS_FILE = Path(__file__).resolve().parent / "airports" / "europe_airports.json"


def _load_path(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load(name: str) -> Any:
    return _load_path(DATA_DIR / name)


def fleet() -> list[dict[str, Any]] | None:
    """Aircraft type reference (ICAO 8643 + curated capacity/range specs)."""
    return _load("fleet.json")


def routes() -> list[dict[str, Any]] | None:
    """EU route network with endpoints mapped to ICAO codes."""
    return _load("routes.json")


def airports() -> list[dict[str, Any]] | None:
    """Enriched EU airport reference (AirportDB, built once)."""
    return _load("airports.json")


def aircraft_types() -> dict[str, list[Any]] | None:
    """ICAO DOC 8643 type designators used by the classifier."""
    return _load("aircraft_types.json")


def airlines() -> dict[str, list[str]] | None:
    """OpenFlights operator reference used by the classifier."""
    return _load("airlines.json")


def _airport_rows() -> list[dict[str, Any]]:
    local = _load("airports.json")
    if isinstance(local, list) and local:
        return local
    bundled = _load_path(AIRPORTS_FILE)
    return bundled if isinstance(bundled, list) else []


@functools.lru_cache(maxsize=1)
def airport_coordinates() -> dict[str, tuple[float, float]]:
    """ICAO → (lat, lon) for every bundled EU airport (cached)."""
    coords: dict[str, tuple[float, float]] = {}
    for row in _airport_rows():
        icao = str(row.get("icao") or row.get("ident") or "").strip().upper()
        lat = row.get("latitude")
        lon = row.get("longitude")
        if icao and isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            coords.setdefault(icao, (float(lat), float(lon)))
    return coords


@functools.lru_cache(maxsize=1)
def iata_to_icao() -> dict[str, str]:
    """IATA → ICAO for the bundled airports (OpenFlights endpoints are IATA)."""
    mapping: dict[str, str] = {}
    for row in _airport_rows():
        iata = str(row.get("iata") or row.get("iata_code") or "").strip().upper()
        icao = str(row.get("icao") or row.get("ident") or "").strip().upper()
        if iata and icao:
            mapping.setdefault(iata, icao)
    return mapping


def route_distance_km(origin: str, destination: str) -> float | None:
    """Great-circle distance between two ICAO codes, or None if unknown."""
    coords = airport_coordinates()
    origin_point = coords.get(origin.upper())
    destination_point = coords.get(destination.upper())
    if not origin_point or not destination_point:
        return None
    return round(_haversine(origin_point, destination_point), 1)


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(h)) * 6371.0
