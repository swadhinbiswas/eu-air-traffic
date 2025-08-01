"""Deterministic synthetic data generation (MOCK_MODE / CI).

When upstream API credentials are missing, collectors fall back to these
generators so the entire pipeline (bronze → silver → gold → DuckDB) can run
end-to-end with reproducible output. Output is seeded so runs are stable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from config.settings import settings

# European airports with realistic frequencies — used as the basis for
# synthetic flights so generated data matches the real dimension table.
_DEFAULT_AIRPORTS: list[dict[str, Any]] = [
    {"icao": "EDDF", "iata": "FRA", "name": "Frankfurt Airport", "iso_country": "DE"},
    {"icao": "EGLL", "iata": "LHR", "name": "London Heathrow", "iso_country": "GB"},
    {"icao": "LFPG", "iata": "CDG", "name": "Paris Charles de Gaulle", "iso_country": "FR"},
    {"icao": "EHAM", "iata": "AMS", "name": "Amsterdam Schiphol", "iso_country": "NL"},
    {"icao": "EDDM", "iata": "MUC", "name": "Munich Airport", "iso_country": "DE"},
    {"icao": "LEMD", "iata": "MAD", "name": "Madrid Barajas", "iso_country": "ES"},
    {"icao": "LEBL", "iata": "BCN", "name": "Barcelona El Prat", "iso_country": "ES"},
    {"icao": "LIRF", "iata": "FCO", "name": "Rome Fiumicino", "iso_country": "IT"},
    {"icao": "LOWW", "iata": "VIE", "name": "Vienna Airport", "iso_country": "AT"},
    {"icao": "LSZH", "iata": "ZRH", "name": "Zurich Airport", "iso_country": "CH"},
]

_AIRLINES: list[dict[str, str]] = [
    {"icao": "DLH", "iata": "LH", "name": "Lufthansa"},
    {"icao": "BAW", "iata": "BA", "name": "British Airways"},
    {"icao": "AFR", "iata": "AF", "name": "Air France"},
    {"icao": "KLM", "iata": "KL", "name": "KLM"},
    {"icao": "IBE", "iata": "IB", "name": "Iberia"},
    {"icao": "RYR", "iata": "FR", "name": "Ryanair"},
    {"icao": "EZY", "iata": "U2", "name": "EasyJet"},
    {"icao": "EIN", "iata": "EI", "name": "Aer Lingus"},
    {"icao": "SWR", "iata": "LX", "name": "Swiss"},
    {"icao": "AUA", "iata": "OS", "name": "Austrian Airlines"},
]

_WEATHER_CONDITIONS = ["Clear", "Clouds", "Rain", "Snow", "Thunderstorm", "Fog", "Mist"]

_SEED_PATTERNS = ("sunny", "stormy", "foggy", "windy", "snowy", "mild")


def _stable(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def _seeded_rng(seed: str) -> Any:
    import random

    return random.Random(_stable(seed))


def _airports() -> list[dict[str, Any]]:
    """European airport base list — prefer the real cached OpenFlights data."""
    cache = settings.project_root / "ingestion" / "airports" / "europe_airports.json"
    if cache.exists():
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
            if raw:
                return [
                    {
                        "icao": row["icao"],
                        "iata": row.get("iata") or row["icao"],
                        "name": row.get("name") or row["icao"],
                        "iso_country": row.get("country") or row["icao"][:2],
                    }
                    for row in raw[:80]
                ]
        except (OSError, ValueError, KeyError):
            pass
    return _DEFAULT_AIRPORTS


def _make_icao(rng: Any, size: int = 6) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(rng.choice(alphabet) for _ in range(size))


def synthetic_for(source: str, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return deterministic synthetic records for a given data source."""
    rows = rows or []
    if source == "airports":
        return rows or [
            {**a, "score": 100 + _stable(a["icao"]) % 150, "type": "large_airport"}
            for a in _airports()
        ]
    if source == "holidays":
        return rows or _synthetic_holidays()
    if source == "fuel":
        return rows or _synthetic_fuel()
    if source == "flights":
        return rows or _synthetic_flights()
    if source == "weather":
        return rows or _synthetic_weather()
    return rows


def _synthetic_holidays() -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    year = now.year
    rows: list[dict[str, Any]] = []
    for country in ("DE", "GB", "FR", "NL", "ES", "IT", "AT", "CH"):
        for month in (1, 4, 5, 12):
            day = {1: 1, 4: 1, 5: 1, 12: 25}[month]
            rows.append(
                {
                    "country": country,
                    "date": f"{year}-{month:02d}-{day:02d}",
                    "name": f"{country} Holiday {month}",
                    "source": "synthetic",
                }
            )
    return rows


def _synthetic_fuel() -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for offset in range(7):
        day = now.date() - timedelta(days=offset)
        rows.append(
            {
                "date": day.isoformat(),
                "region": "EU",
                "price_per_litre": round(0.95 + (offset % 3) * 0.03, 3),
                "currency": "EUR",
                "source": "synthetic",
            }
        )
    return rows


def _synthetic_flights(count: int = 400) -> list[dict[str, Any]]:
    airports = _airports()
    rng = _seeded_rng(f"flights-{datetime.now(UTC).strftime('%Y-%m-%d')}")
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []

    for index in range(count):
        origin = airports[rng.randrange(len(airports))]
        destination = airports[rng.randrange(len(airports))]
        airline = _AIRLINES[rng.randrange(len(_AIRLINES))]
        scheduled = now + timedelta(hours=rng.randint(-24, 2))
        status = rng.choices(["scheduled", "landed", "cancelled"], weights=[30, 60, 10])[0]
        delay = rng.randint(-15, 220) if status != "cancelled" else 0

        rows.append(
            {
                "flight_id": f"{airline['iata']}{rng.randint(100, 9999)}",
                "callsign": f"{airline['icao']}{rng.randint(10, 99)}{chr(65 + rng.randint(0, 25))}{chr(65 + rng.randint(0, 25))}",
                "airline_icao": airline["icao"],
                "airline_name": airline["name"],
                "departure_icao": origin["icao"],
                "departure_iata": origin["iata"],
                "arrival_icao": destination["icao"],
                "arrival_iata": destination["iata"],
                "scheduled_departure": (scheduled).isoformat(),
                "scheduled_arrival": (scheduled + timedelta(hours=2)).isoformat(),
                "actual_departure": (scheduled + timedelta(minutes=delay)).isoformat()
                if status != "cancelled"
                else None,
                "actual_arrival": (scheduled + timedelta(hours=2, minutes=delay)).isoformat()
                if status != "cancelled"
                else None,
                "status": status,
                "delay_minutes": max(delay, 0) if status != "cancelled" else 0,
                "cancelled": status == "cancelled",
                "source": "synthetic",
                "collected_at": now.isoformat(),
                "ingestion_index": index,
            }
        )
    return rows


def _synthetic_weather(count: int = 60) -> list[dict[str, Any]]:
    airports = _airports()
    rng = _seeded_rng(f"weather-{datetime.now(UTC).strftime('%Y-%m-%d')}")
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        airport = airports[rng.randrange(len(airports))]
        stamp = now - timedelta(hours=rng.randint(0, 48))
        rows.append(
            {
                "station_icao": airport["icao"],
                "timestamp": stamp.isoformat(),
                "temperature_c": round(rng.uniform(-5, 30), 1),
                "humidity_pct": rng.randint(30, 99),
                "wind_speed_ms": round(rng.uniform(0, 20), 1),
                "visibility_m": rng.randint(200, 12000),
                "condition": rng.choice(_WEATHER_CONDITIONS),
                "pressure_hpa": rng.randint(980, 1040),
                "source": "synthetic",
            }
        )
    return rows
