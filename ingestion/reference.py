"""Local reference data — near-static datasets shipped with the repo.

Airports, routes, the fleet and airlines change on the order of months, so the
collectors must not download them on every run. ``scripts.build_reference_data``
materialises them once under ``services/data/`` and this module reads them.

Each helper returns ``None`` when the file is absent so the caller can fall back
to a live fetch (and the build script can regenerate it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load(name: str) -> Any:
    path = DATA_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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
