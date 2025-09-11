"""Aircraft classification — turn raw ADS-B into the operational picture.

The platform is not just about airline traffic: it separates **cargo, military,
passenger, business, private, helicopters, gliders, drones and balloons** across
the whole European airspace. This module is data-driven, not a handful of codes:

* **ICAO DOC 8643 type designators** (``services/data/aircraft_types.json``,
  ~2,800 types) give the authoritative physical airframe type (landplane,
  helicopter, gyrocopter, …), engine count/type and wake category.
* **OpenFlights airline designators** (``services/data/airlines.json``, ~5,800
  operators) give the operator, country and an operator category
  (passenger / cargo / military / government).
* **ADS-B emitter category** (``A7`` rotorcraft, ``B1`` glider, ``B6`` UAV, …)
  covers the long tail of non-airline traffic.
* **Curated supplements** for the few widely-used codes missing from the open
  datasets and for tactical military callsigns that never appear in any airline
  table.

Everything is deterministic and explicit — no hidden model. Priority order:
ground/obstacle → military → cargo → physical class (helicopter/glider/drone/
balloon/ultralight) → business → passenger → private → other.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent / "data"

# ── ADS-B emitter category (DO-260B) → physical kind ────────────────────────
EMITTER_CATEGORY: dict[str, str] = {
    "A0": "unknown",
    "A1": "light",
    "A2": "small",
    "A3": "large",
    "A4": "large",
    "A5": "heavy",
    "A6": "high_performance",
    "A7": "helicopter",
    "B0": "unknown",
    "B1": "glider",
    "B2": "balloon",
    "B3": "parachutist",
    "B4": "ultralight",
    "B6": "drone",
    "B7": "space",
    "C1": "ground_emergency",
    "C2": "ground_service",
    "C3": "obstacle",
    "C4": "obstacle",
    "C5": "obstacle",
}

# ── Tactical military callsign prefixes (not in any airline table) ──────────
MILITARY_CALLSIGN_PREFIXES = {
    "RCH",
    "EVAC",
    "CMB",
    "HKY",
    "JAKE",
    "IRON",
    "RAIDER",
    "TITAN",
    "MAGMA",
    "SPAR",
    "SAM",
    "EXEC",
    "RRR",
    "NAF",
    "GAF",
    "FAF",
    "IAM",
    "AME",
    "HAF",
    "PLF",
    "CFC",
    "BAF",
    "TUAF",
    "HUF",
    "FNF",
    "SVF",
    "DAF",
    "NOW",
    "ASY",
    "GAF",
    "CFC",
    "NATO",
    "AWACS",
}

# Cargo operators as a safety net when the callsign is missing from the table.
CARGO_CALLSIGN_PREFIXES = {
    "FDX",
    "UPS",
    "GTI",
    "CLX",
    "BCS",
    "DHL",
    "BOX",
    "GEC",
    "AZG",
    "CKK",
    "CKS",
    "ABX",
    "PAC",
    "SQC",
    "MPH",
    "EAT",
    "SWN",
    "NPT",
}

# Military/government ICAO24 address blocks (country allocations).
MILITARY_HEX_PREFIXES = ("AE", "AF", "43C")  # US Air Force, US Army, UK military

# Curated supplements for codes the open datasets miss.
HELICOPTER_TYPES = {
    "H125",
    "H130",
    "H135",
    "H145",
    "H155",
    "H160",
    "H175",
    "H215",
    "H225",
    "B06",
    "B105",
    "B206",
    "B212",
    "B222",
    "B230",
    "B407",
    "B412",
    "B429",
    "B430",
    "B505",
    "S55",
    "S61",
    "S76",
    "S92",
    "R22",
    "R44",
    "R66",
    "AS32",
    "AS35",
    "AS55",
    "AS65",
    "MD52",
    "MD60",
    "MD90",
    "AW09",
    "AW19",
}
BUSINESS_JET_TYPES = {
    "GLF",
    "GLF3",
    "GLF4",
    "GLF5",
    "GLF6",
    "G150",
    "G280",
    "G550",
    "G650",
    "CL30",
    "CL35",
    "CL60",
    "C25A",
    "C25B",
    "C25C",
    "C56X",
    "C68A",
    "C700",
    "C750",
    "E50P",
    "E55P",
    "E35L",
    "LJ35",
    "LJ45",
    "LJ60",
    "LJ75",
    "FA7X",
    "FA8X",
    "F900",
    "F2TH",
    "H25B",
    "HDJT",
    "PC24",
    "C525",
    "C510",
    "C550",
    "C560",
    "C650",
    "C680",
    "GL7T",
    "G100",
    "BE40",
    "BE20",
}
FREIGHTER_TYPES = {
    "B77F",
    "B76F",
    "B74F",
    "B75F",
    "B73F",
    "B72F",
    "B74Y",
    "BDSF",
    "A30F",
    "A31F",
    "A32F",
    "A33F",
    "A3ST",
    "A225",
    "A124",
    "MD11",
    "DC10",
    "IL76",
    "AN12",
    "AN24",
    "AN26",
    "AN32",
}
AIRLINE_PREFIX_HINTS = {
    "DLH",
    "BAW",
    "AFR",
    "KLM",
    "IBE",
    "RYR",
    "EZY",
    "EIN",
    "SWR",
    "AUA",
    "TAP",
    "SAS",
    "WZZ",
    "VLG",
    "EWG",
    "THY",
    "PGT",
    "LOT",
    "FIN",
    "NAX",
    "AEE",
    "LDA",
    "CSA",
    "AAL",
    "UAL",
    "DAL",
    "SWA",
    "JBU",
    "ACA",
    "AMX",
    "UAE",
    "QTR",
    "ETD",
    "SIA",
    "CPA",
    "JAL",
    "ANA",
    "KAL",
    "AIC",
    "THA",
}

_FREIGHTER_CODE = re.compile(r"^[A-Z]{1,2}\d{2,3}F$")


# ── reference data ──────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def aircraft_types() -> dict[str, list[Any]]:
    """ICAO type code → [name, airframe, engines, engine_type, wtc, category]."""
    try:
        return json.loads((_DATA_DIR / "aircraft_types.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):  # pragma: no cover - missing data falls back
        return {}


@lru_cache(maxsize=1)
def airlines() -> dict[str, list[str]]:
    """ICAO airline code → [name, country, callsign, category]."""
    try:
        return json.loads((_DATA_DIR / "airlines.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):  # pragma: no cover
        return {}


def type_info(aircraft_type: str | None) -> dict[str, Any] | None:
    """Look up an ICAO type designator."""
    if not aircraft_type:
        return None
    row = aircraft_types().get(aircraft_type.strip().upper())
    if not row or len(row) < 6:
        return None
    return {
        "type_name": row[0],
        "manufacturer": str(row[0]).split()[0] if row[0] else None,
        "airframe": row[1],
        "engines": row[2],
        "engine_type": row[3],
        "wake_category": row[4],
        "type_category": row[5],
    }


def operator_info(callsign: str | None) -> dict[str, Any] | None:
    """Look up the operator from a flight's 3-letter ICAO callsign prefix.

    A flight callsign is ``<3-letter airline><flight number>`` and always carries
    a digit (``DLH400``). Registrations such as ``GBTVX`` or ``N123AB`` are not
    callsigns and must not match an operator.
    """
    if not callsign:
        return None
    cs = callsign.strip().upper()
    if len(cs) < 3 or not cs[:3].isalpha() or not any(ch.isdigit() for ch in cs):
        return None
    row = airlines().get(cs[:3])
    if not row or len(row) < 4:
        return None
    return {"operator_name": row[0], "operator_country": row[1], "operator_category": row[3]}


def emitter_class(category: Any) -> str:
    """Physical kind from the ADS-B emitter category (``A7`` → ``helicopter``)."""
    if not category:
        return "unknown"
    return EMITTER_CATEGORY.get(str(category).strip().upper(), "unknown")


def classify(
    callsign: str | None,
    aircraft_type: str | None,
    category: Any = None,
    registration: str | None = None,
    icao24: str | None = None,
) -> dict[str, Any]:
    """Classify one aircraft into the operational + physical classes."""
    cs = (callsign or "").strip().upper()
    callsign3 = cs[:3]
    emitter = emitter_class(category)
    info = type_info(aircraft_type)
    op = operator_info(callsign)
    type_code = (aircraft_type or "").strip().upper()

    type_category = info["type_category"] if info else ""
    airframe = info["airframe"] if info else "unknown"
    engines = (info or {}).get("engines") or 0
    op_category = op["operator_category"] if op else ""
    hex_prefix = (icao24 or "").strip().upper()[:3]

    is_military = bool(
        op_category == "military"
        or callsign3 in MILITARY_CALLSIGN_PREFIXES
        or type_category == "military"
        or any(hex_prefix.startswith(p) for p in MILITARY_HEX_PREFIXES)
    )
    is_cargo = bool(
        op_category == "cargo"
        or callsign3 in CARGO_CALLSIGN_PREFIXES
        or type_category == "cargo"
        or type_code in FREIGHTER_TYPES
        or _FREIGHTER_CODE.match(type_code)
    )

    # Priority: ground → explicit role → physical airframe → operator/size.
    if emitter in {"ground_emergency", "ground_service", "obstacle"}:
        klass = "ground"
    elif is_military:
        klass = "military"
    elif is_cargo:
        klass = "cargo"
    elif emitter == "helicopter" or airframe == "helicopter" or type_code in HELICOPTER_TYPES:
        klass = "helicopter"
    elif emitter == "glider" or type_category == "glider":
        klass = "glider"
    elif emitter == "drone" or type_category == "drone":
        klass = "drone"
    elif emitter == "balloon":
        klass = "balloon"
    elif (
        emitter in {"ultralight", "parachutist"}
        or airframe == "gyrocopter"
        or type_code in {"GYRO", "AUTOGYRO"}
    ):
        klass = "ultralight"
    elif type_category == "business" or type_code in BUSINESS_JET_TYPES:
        klass = "business"
    elif airframe == "landplane" and engines <= 1 and (info or {}).get("engine_type") == "piston":
        klass = "private"  # light single-engine types are never airline traffic
    elif (
        op_category == "passenger"
        or emitter in {"large", "heavy", "high_performance"}
        or callsign3 in AIRLINE_PREFIX_HINTS
        or (airframe == "landplane" and engines >= 2)
    ):
        klass = "passenger"
    elif emitter in {"light", "small"} or airframe == "landplane":
        klass = "private"
    else:
        klass = "other"

    return {
        "aircraft_class": klass,
        "emitter_class": emitter,
        "is_cargo": klass == "cargo",
        "is_military": klass == "military",
        "operator_name": op["operator_name"] if op else None,
        "operator_country": op["operator_country"] if op else None,
        "operator_category": op_category or None,
        "type_name": info["type_name"] if info else None,
        "manufacturer": info["manufacturer"] if info else None,
        "airframe": airframe,
        "wake_category": info["wake_category"] if info else None,
    }
