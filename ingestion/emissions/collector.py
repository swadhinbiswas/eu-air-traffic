"""Aircraft emissions data collection.

Collects carbon emissions data including CO2 estimates per flight,
fuel burn rates, and emission factors. Uses ICAO Carbon Emissions
Calculator methodology with fallback to estimated values.

Reference: https://www.icao.int/environmental-protection/CarbonOffset/
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from config.logging import logger
from config.settings import Settings
from ingestion.base import Collector

# Emission factors (kg CO2 per liter of jet fuel)
# Source: ICAO default emission factor for jet fuel
EMISSION_FACTOR_KG_PER_LITER = 2.52  # kg CO2 per kg fuel, fuel density ~0.8 kg/L
FUEL_DENSITY_KG_PER_LITER = 0.8

# Fuel burn rates by aircraft type (liters per hour)
FUEL_BURN_RATES = {
    "A320": 2500,
    "A321": 2700,
    "A319": 2300,
    "A332": 5200,
    "A333": 5500,
    "A359": 5600,
    "A388": 11000,
    "B737": 2400,
    "B738": 2600,
    "B748": 10000,
    "B752": 3100,
    "B763": 4300,
    "B772": 6200,
    "B773": 6600,
    "B788": 5400,
    "B789": 5600,
    "E190": 1200,
    "CRJ9": 1100,
    "AT76": 600,
}


class EmissionsCollector(Collector):
    """Collects aircraft emissions data and emission factors."""

    name = "emissions"
    source = "emissions"

    ICAO_CALCULATOR_URL = "https://applications.icao.int/cecarbon/calculator.cfm"

    def __init__(self, app_settings: Settings | None = None) -> None:
        super().__init__(app_settings)

    def fetch(self, start: datetime) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        # Generate emission factors and estimates
        for aircraft_type, burn_rate in FUEL_BURN_RATES.items():
            # Calculate hourly emissions
            hourly_fuel_liters = burn_rate
            hourly_fuel_kg = hourly_fuel_liters * FUEL_DENSITY_KG_PER_LITER
            hourly_co2_kg = (
                hourly_fuel_kg * EMISSION_FACTOR_KG_PER_LITER / 1000
            )  # Convert to tonnes

            rows.append(
                {
                    "aircraft_type": aircraft_type,
                    "fuel_burn_liters_per_hour": burn_rate,
                    "fuel_burn_kg_per_hour": round(hourly_fuel_kg, 1),
                    "co2_kg_per_hour": round(hourly_co2_kg * 1000, 1),
                    "co2_tonnes_per_hour": round(hourly_co2_kg, 3),
                    "emission_factor_kg_per_kg_fuel": EMISSION_FACTOR_KG_PER_LITER,
                    "fuel_density_kg_per_liter": FUEL_DENSITY_KG_PER_LITER,
                    "source": "icao_methodology",
                    "collected_at": datetime.now(UTC).isoformat(),
                }
            )

        return self._mock(rows) if self.settings.mock_mode else rows


if __name__ == "__main__":
    collector = EmissionsCollector()
    count = collector.run()
    logger.info("Collected %s emissions records", count)
