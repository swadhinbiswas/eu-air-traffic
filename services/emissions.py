"""Aircraft emissions — the one derived data product.

There is no emissions API; CO₂ is estimated from an aircraft's live telemetry
using ICAO Carbon Emissions Calculator methodology (fuel burn rate × fuel
density × emission factor). The factor table lives in the reference topic so the
Gold layer can re-derive tonnes from flight distance.
"""

from __future__ import annotations

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


def emission_estimate(aircraft_type: str | None) -> tuple[float, float, bool]:
    """Return ``(fuel_kg_per_hour, co2_kg_per_hour, estimated)`` for a type.

    Ground vehicles resolve to a confident zero. Unknown types fall back to an
    A320 narrowbody rate flagged as estimated, so the dashboard never presents
    a guess as a measurement.
    """
    code = (aircraft_type or "").upper()
    if code in GROUND_TYPES:
        return 0.0, 0.0, False
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
    """Add estimated fuel/CO₂ rates to an aircraft position row (in place)."""
    fuel, co2, estimated = emission_estimate(row.get("aircraft_type"))
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
