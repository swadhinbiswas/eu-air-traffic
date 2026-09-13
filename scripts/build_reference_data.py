"""Build the aircraft/operator reference data used by the classifier.

Downloads two authoritative open datasets and writes compact JSON under
``services/data/``:

* **ICAO DOC 8643** aircraft type designators (mirrored by ``tar1090-db``):
  type code → name, physical type (landplane/helicopter/…), engine count/type,
  wake category.
* **OpenFlights airlines.dat**: ICAO airline designator → name, country, callsign
  and a derived operator category (passenger / cargo / military / government /
  other).

Run whenever the upstream datasets change::

    python -m scripts.build_reference_data

The generated files are committed so the collector and CI need no network.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from pathlib import Path

import requests

from config.logging import logger, setup_logging

DATA_DIR = Path(__file__).resolve().parent.parent / "services" / "data"
TYPES_URL = (
    "https://raw.githubusercontent.com/wiedehopf/tar1090-db/master/db/icao_aircraft_types2.js"
)
AIRLINES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
PLANES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/planes.dat"
ROUTES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"
EU_AIRPORTS_FILE = (
    Path(__file__).resolve().parent.parent / "ingestion" / "airports" / "europe_airports.json"
)

# ICAO 8643 description: first letter = physical airframe type.
AIRFRAME = {
    "L": "landplane",
    "H": "helicopter",
    "G": "gyrocopter",
    "S": "seaplane",
    "A": "amphibian",
    "T": "tiltrotor",
}
ENGINE = {"P": "piston", "T": "turboprop", "J": "jet", "E": "electric"}

MILITARY_NAME_HINTS = (
    "air force",
    "airforce",
    "military",
    "army",
    "navy",
    "naval",
    "marine",
    "national guard",
    "government",
    "state",
    "police",
    "coast guard",
    "border",
    "customs",
    "ministry of defence",
    "ministry of defense",
    "gendarmerie",
    "luftwaffe",
    "aerobatic",
    "squadron",
    "u.s.",
    "united states air",
)
CARGO_NAME_HINTS = (
    "cargo",
    "freight",
    "courier",
    "logistics",
    "parcel",
    "air transport international",
    "asl airlines",
    "west atlantic",
    "swift air",
    "amerijet",
    "kalitta",
    "polar air",
    "atlas air",
    "cargolux",
    "aerologic",
    "bluebird cargo",
    "fedex",
    "ups airlines",
    "dhl",
    "tnt airways",
)

# Operator category → military/government detection for aircraft type names.
MILITARY_TYPE_HINTS = (
    "hercules",
    "globemaster",
    "galaxy",
    "osprey",
    "apache",
    "black hawk",
    "chinook",
    "eurofighter",
    "typhoon",
    "rafale",
    "gripen",
    "tornado",
    "hornet",
    "falcon (dassault)",
    "c-130",
    "c-17",
    "c-5",
    "a400",
    "a-400",
    "predator",
    "reaper",
    "global hawk",
    "sentinel",
    "awacs",
    "kawasaki p-1",
)

BUSINESS_HINTS = (
    "gulfstream",
    "bombardier global",
    "bombardier challenger",
    "learjet",
    "dassault falcon",
    "citation",
    "phenom",
    "legacy",
    "praetor",
    "hawker",
    "piaggio",
    "pilatus pc-12",
    "king air",
    "beechcraft 350",
    "cessna citation",
    "embraer praetor",
    "global 5000",
    "global 6000",
    "global 7500",
)

GLIDER_HINTS = ("glider", "sailplane", "discus", "ventus", "duo discus", "ask-", "dg-", "ls-")
DRONE_HINTS = ("unmanned", "drone", "uav", "mq-", "rq-", "surveyor", "aerosonde")


def _fetch_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def _decode_json_maybe_gzip(raw: bytes) -> dict:
    """tar1090 files are committed gzip-compressed; accept plain JSON too."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json.loads(gzip.decompress(raw).decode("utf-8"))


def _parse_designator(desc: str) -> dict[str, object]:
    """Decode an ICAO 8643 designator like ``L2J`` / ``H2T`` / ``L1P``."""
    match = re.match(r"^([LHGSAT])(\d*)([PTJE]?)$", (desc or "").strip())
    if not match:
        return {"airframe": "unknown", "engines": None, "engine_type": None}
    airframe_letter, engines, engine_type = match.groups()
    return {
        "airframe": AIRFRAME.get(airframe_letter, "unknown"),
        "engines": int(engines) if engines else None,
        "engine_type": ENGINE.get(engine_type),
    }


def _type_category(type_code: str, name: str) -> str:
    """Derive an operational hint for an aircraft type from its name/code."""
    low = name.lower()
    if any(h in low for h in MILITARY_TYPE_HINTS):
        return "military"
    if any(h in low for h in DRONE_HINTS):
        return "drone"
    if any(h in low for h in GLIDER_HINTS):
        return "glider"
    if re.match(r"^[A-Z]{1,2}\d{2,3}F$", type_code.upper()):
        return "cargo"  # ICAO freighter suffix convention (B77F, A32F, MD11F)
    if any(h in low for h in BUSINESS_HINTS):
        return "business"
    return ""


def build_aircraft_types() -> int:
    raw = _fetch_bytes(TYPES_URL)
    payload = _decode_json_maybe_gzip(raw)
    out: dict[str, list[object]] = {}
    for type_code, value in payload.items():
        if not isinstance(value, list) or len(value) < 3:
            continue
        name, desc, wtc = value[0], value[1], value[2]
        parsed = _parse_designator(str(desc))
        out[str(type_code).upper()] = [
            str(name),
            parsed["airframe"],
            parsed["engines"],
            parsed["engine_type"],
            str(wtc),
            _type_category(str(type_code), str(name)),
        ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "aircraft_types.json").write_text(
        json.dumps(out, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    logger.info("[reference] %s aircraft types → %s", len(out), DATA_DIR / "aircraft_types.json")
    return len(out)


def _operator_category(name: str, callsign: str) -> str:
    haystack = f"{name} {callsign}".lower()
    if any(h in haystack for h in MILITARY_NAME_HINTS):
        return "military"
    if any(h in haystack for h in CARGO_NAME_HINTS):
        return "cargo"
    if any(k in haystack for k in ("government", "ministry", "authority", "administration")):
        return "government"
    return "passenger"


def build_airlines() -> int:
    raw = _fetch_bytes(AIRLINES_URL).decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    out: dict[str, list[str]] = {}
    for row in reader:
        if len(row) < 7:
            continue
        name, icao, callsign, country = row[1], row[4], row[5], row[6]
        if not icao or icao in {"-", "N/A", "\\N", "NA"}:
            continue
        clean = lambda value: "" if value in {"\\N", "-"} else value  # noqa: E731
        out[icao.upper()] = [
            clean(name),
            clean(country),
            clean(callsign),
            _operator_category(clean(name), clean(callsign)),
        ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "airlines.json").write_text(
        json.dumps(out, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    logger.info("[reference] %s airlines → %s", len(out), DATA_DIR / "airlines.json")
    return len(out)


def _europe_codes() -> tuple[set[str], dict[str, str]]:
    """Return (ICAO set, IATA→ICAO map) for the EU airport reference."""
    try:
        rows = json.loads(EU_AIRPORTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), {}
    icao = {str(r["icao"]).upper() for r in rows if r.get("icao")}
    iata_to_icao = {
        str(r["iata"]).upper(): str(r["icao"]).upper()
        for r in rows
        if r.get("iata") and r.get("icao")
    }
    return icao, iata_to_icao


def _europe_points() -> dict[str, tuple[float, float]]:
    """ICAO → (lat, lon) for the EU airport reference."""
    try:
        rows = json.loads(EU_AIRPORTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    points: dict[str, tuple[float, float]] = {}
    for row in rows:
        icao = str(row.get("icao") or "").upper()
        lat, lon = row.get("latitude"), row.get("longitude")
        if icao and isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            points.setdefault(icao, (float(lat), float(lon)))
    return points


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(h)) * 6371.0


def _fleet_specs() -> dict[str, dict[str, object]]:
    """Capacity/range specs from the curated fleet table (best effort)."""
    from ingestion.aircraft.collector import AIRCRAFT_TYPES

    return AIRCRAFT_TYPES


def build_fleet() -> int:
    """OpenFlights fleet reference (planes.dat) with curated specs merged in."""
    specs = _fleet_specs()
    raw = _fetch_bytes(PLANES_URL).decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in reader:
        if len(row) < 3:
            continue
        name, iata, icao = row[0].strip(), row[1].strip(), row[2].strip()
        code = icao.upper()
        if not code or code in {"\\N", "-"} or code in seen:
            continue
        seen.add(code)
        spec = specs.get(code, {})
        out.append(
            {
                "type_icao": code,
                "type_iata": "" if iata in {"\\N", "-"} else iata,
                "name": name,
                "manufacturer": spec.get("manufacturer"),
                "family": spec.get("family"),
                "engine": spec.get("engine"),
                "capacity": spec.get("capacity"),
                "range_km": spec.get("range_km"),
            }
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "fleet.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    logger.info("[reference] %s aircraft types → %s", len(out), DATA_DIR / "fleet.json")
    return len(out)


def build_routes() -> int:
    """OpenFlights routes.dat filtered to routes touching an EU airport.

    ``routes.dat`` identifies endpoints by IATA code; both endpoints are mapped
    to ICAO so the route network joins cleanly to ``dim_airport``.
    """
    europe_icao, iata_to_icao = _europe_codes()
    points = _europe_points()
    raw = _fetch_bytes(ROUTES_URL).decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    out: list[dict[str, object]] = []
    for row in reader:
        if len(row) < 9:
            continue
        airline = row[0].strip()
        origin = iata_to_icao.get(row[2].strip().upper(), row[2].strip().upper())
        dest = iata_to_icao.get(row[4].strip().upper(), row[4].strip().upper())
        if origin not in europe_icao and dest not in europe_icao:
            continue
        stops = row[7].strip()
        origin_point, dest_point = points.get(origin), points.get(dest)
        distance = (
            round(_haversine_km(origin_point, dest_point), 1)
            if origin_point and dest_point
            else None
        )
        out.append(
            {
                "airline": airline,
                "origin": origin,
                "destination": dest,
                "stops": int(stops) if stops.isdigit() else 0,
                "equipment": row[8].strip(),
                "distance_km": distance,
            }
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "routes.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    logger.info("[reference] %s EU routes → %s", len(out), DATA_DIR / "routes.json")
    return len(out)


def main() -> int:
    setup_logging()
    build_aircraft_types()
    build_airlines()
    build_fleet()
    build_routes()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
