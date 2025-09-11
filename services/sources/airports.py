"""EU airport reference lookup shared by the live sources.

Reads the curated OpenFlights/OurAirports export once and exposes fast helpers
for coordinates and METAR identifiers. The same file seeds the reference
Kafka topic, so coordinates never come from a build-time DuckDB dependency.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from config.logging import logger

_AIRPORT_FILE = (
    Path(__file__).resolve().parents[2] / "ingestion" / "airports" / "europe_airports.json"
)


@lru_cache(maxsize=1)
def europe_airports() -> list[dict[str, Any]]:
    """Load the EU airport reference dataset (1,658 rows) or an empty list."""
    try:
        payload = json.loads(_AIRPORT_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
    except (OSError, ValueError) as exc:  # pragma: no cover - missing dataset
        logger.warning("[airports] reference dataset unavailable: %s", exc)
    return []


@lru_cache(maxsize=1)
def airport_coordinates(limit: int = 400) -> list[dict[str, Any]]:
    """Return ``[{icao, latitude_deg, longitude_deg, name}]`` up to ``limit``."""
    rows: list[dict[str, Any]] = []
    for airport in europe_airports():
        icao = airport.get("icao")
        lat = airport.get("latitude")
        lon = airport.get("longitude")
        if not icao or lat is None or lon is None:
            continue
        rows.append(
            {
                "icao": str(icao).upper(),
                "latitude_deg": float(lat),
                "longitude_deg": float(lon),
                "name": airport.get("name") or icao,
                "country": airport.get("country"),
            }
        )
        if len(rows) >= limit:
            break
    return rows


@lru_cache(maxsize=1)
def airport_name_index() -> dict[str, dict[str, Any]]:
    """ICAO → airport metadata for enrichment."""
    return {str(a["icao"]).upper(): a for a in europe_airports() if a.get("icao")}
