"""Aircraft emissions — the one derived data product.

There is no emissions API; CO₂ is estimated from an aircraft's live telemetry.
When the airframe is in the precomputed OpenAP table (TU Delft's open aircraft
performance model), fuel flow comes from actual kinematics — mass, altitude,
speed and vertical rate — instead of a flat per-type rate. Unknown types and
rows without telemetry keep the ICAO Carbon Emissions Calculator table, and
unknown-airframe fallbacks stay flagged as estimates.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ingestion.emissions.collector import (
    EMISSION_FACTOR_KG_PER_LITER,
    FUEL_BURN_RATES,
    FUEL_DENSITY_KG_PER_LITER,
)

DEFAULT_TYPE = "A320"

# Transponder pseudo-types for ground vehicles and fixed installations. They
# emit ADS-B but burn no jet fuel, so they must never receive the fallback
# airliner rate (previously a TWR/GND contact counted as a full A320).
GROUND_TYPES = frozenset({"GND", "GRND", "TWR", "TOWER"})

_OPENAP_PATH = Path(__file__).resolve().parent / "data" / "emission_openap.json"


@lru_cache(maxsize=1)
def _openap() -> dict[str, Any]:
    """Precomputed OpenAP grid (``scripts/build_openap_emissions.py``)."""
    try:
        payload = json.loads(_OPENAP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _nearest_index(bands: list[float], value: float) -> int:
    return min(range(len(bands)), key=lambda index: abs(float(bands[index]) - value))


def _modelled_fuel(
    code: str,
    altitude: float | None,
    speed_kt: float | None,
    vertical_rate: float | None,
    on_ground: bool,
) -> float | None:
    """OpenAP fuel flow in kg/h, or None when the type/telemetry has no model."""
    payload = _openap()
    table = (payload.get("types") or {}).get(code)
    if not table:
        return None
    if on_ground:
        return float(table["ground"])
    if altitude is None and speed_kt is None:
        return None
    meta = payload.get("_meta") or {}
    altitude = float(altitude if altitude is not None else meta["alt_bands_ft"][-1])
    speed_kt = float(speed_kt if speed_kt is not None else meta["speed_bands_kt"][-1])
    vs = float(vertical_rate or 0.0)
    return float(
        table["flow"][_nearest_index(meta["vs_bands_fpm"], vs)][
            _nearest_index(meta["alt_bands_ft"], altitude)
        ][_nearest_index(meta["speed_bands_kt"], speed_kt)]
    )


def emission_estimate(
    aircraft_type: str | None,
    altitude: float | None = None,
    speed_kt: float | None = None,
    vertical_rate: float | None = None,
    on_ground: bool = False,
) -> tuple[float, float, bool]:
    """Return ``(fuel_kg_per_hour, co2_kg_per_hour, estimated)``.

    Ground vehicles resolve to a confident zero. A type in the OpenAP table
    uses the kinematic model; anything else falls back to the static table and
    then to an A320 narrowbody rate flagged as estimated, so the dashboard
    never presents a guess as a measurement.
    """
    code = (aircraft_type or "").upper()
    if code in GROUND_TYPES:
        return 0.0, 0.0, False
    modelled = _modelled_fuel(code, altitude, speed_kt, vertical_rate, on_ground)
    if modelled is not None:
        fuel = round(modelled, 1)
        return fuel, round(fuel * EMISSION_FACTOR_KG_PER_LITER, 1), False
    rate_lph = FUEL_BURN_RATES.get(code)
    estimated = rate_lph is None
    if rate_lph is None:
        rate_lph = FUEL_BURN_RATES[DEFAULT_TYPE]
    fuel = round(rate_lph * FUEL_DENSITY_KG_PER_LITER, 1)
    co2 = round(fuel * EMISSION_FACTOR_KG_PER_LITER, 1)
    return fuel, co2, estimated


def fuel_burn_kg_per_hour(aircraft_type: str | None) -> float:
    """Hourly fuel burn in kg for a type, falling back to a narrowbody."""
    fuel, _, _ = emission_estimate(aircraft_type)
    return fuel


def co2_kg_per_hour(aircraft_type: str | None) -> float:
    """Instantaneous CO₂ emission rate in kg/hour for a type."""
    _, co2, _ = emission_estimate(aircraft_type)
    return co2


def enrich_emissions(row: dict[str, Any]) -> dict[str, Any]:
    """Add fuel/CO₂ rates to an aircraft position row (in place).

    Uses the OpenAP kinematic model when the type is known and the row carries
    telemetry; otherwise the static per-type table.
    """
    fuel, co2, estimated = emission_estimate(
        row.get("aircraft_type"),
        altitude=row.get("altitude"),
        speed_kt=row.get("velocity"),
        vertical_rate=row.get("vertical_rate"),
        on_ground=bool(row.get("on_ground")),
    )
    row["fuel_burn_kg_per_hour"] = fuel
    row["co2_kg_per_hour"] = co2
    row["co2_estimated"] = estimated
    return row


def emission_factors() -> list[dict[str, Any]]:
    """Reference rows: per-type hourly fuel and CO₂ (used by the live + Gold)."""
    return [
        {
            "aircraft_type": aircraft_type,
            "fuel_burn_liters_per_hour": rate_lph,
            "fuel_burn_kg_per_hour": round(rate_lph * FUEL_DENSITY_KG_PER_LITER, 1),
            "co2_kg_per_hour": round(
                rate_lph * FUEL_DENSITY_KG_PER_LITER * EMISSION_FACTOR_KG_PER_LITER, 1
            ),
            "co2_tonnes_per_hour": round(
                rate_lph * FUEL_DENSITY_KG_PER_LITER * EMISSION_FACTOR_KG_PER_LITER / 1000, 3
            ),
            "emission_factor_kg_per_kg_fuel": EMISSION_FACTOR_KG_PER_LITER,
            "fuel_density_kg_per_liter": FUEL_DENSITY_KG_PER_LITER,
            "source": "icao_methodology",
        }
        for aircraft_type, rate_lph in sorted(FUEL_BURN_RATES.items())
    ]
