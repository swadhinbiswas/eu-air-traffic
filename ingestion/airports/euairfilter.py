"""European airport list filtering helper (OpenFlights source).

Used by :class:`~ingestion.airports.collector.AirportCollector`.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import requests

from config.settings import settings


class EuropeAirportFilter:
    """Fetches and filters the OpenFlights airport database to Europe."""

    OPENFLIGHTS_URL = (
        "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
    )

    EUROPE_ICAO_PREFIXES = frozenset(settings.europe_icao_prefixes)

    @staticmethod
    def safe_float(value: str) -> float | None:
        if value in (r"\N", "", None):
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def safe_int(value: str) -> int | None:
        if value in (r"\N", "", None):
            return None
        try:
            return int(float(value))
        except ValueError:
            return None

    @classmethod
    def is_europe_airport(cls, icao: str) -> bool:
        return bool(icao) and icao != r"\N" and icao[:2] in cls.EUROPE_ICAO_PREFIXES

    @classmethod
    def parse_airport(cls, row: list[str]) -> dict:
        return {
            "airport_id": cls.safe_int(row[0]),
            "name": row[1],
            "city": row[2],
            "country": row[3],
            "iata": None if row[4] == r"\N" else row[4],
            "icao": row[5],
            "latitude": cls.safe_float(row[6]),
            "longitude": cls.safe_float(row[7]),
            "altitude_ft": cls.safe_int(row[8]),
            "timezone_offset": cls.safe_float(row[9]),
            "timezone": None if row[11] == r"\N" else row[11],
            "airport_type": None if row[12] == r"\N" else row[12],
            "source": None if row[13] == r"\N" else row[13],
        }

    @classmethod
    def fetch(cls, url: str | None = None) -> list[dict]:
        response = requests.get(url or cls.OPENFLIGHTS_URL, timeout=60)
        response.raise_for_status()

        airports: list[dict] = []
        for row in csv.reader(response.text.splitlines()):
            if len(row) < 14:
                continue
            if not cls.is_europe_airport(row[5]):
                continue
            airports.append(cls.parse_airport(row))

        airports.sort(key=lambda airport: airport["icao"])
        return airports

    @staticmethod
    def save(airports: list[dict], output_path: str | Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(airports, file, indent=2, ensure_ascii=False)
        return output_path

    @staticmethod
    def preview(airports: list[dict], limit: int = 5) -> None:
        for airport in airports[:limit]:
            print(f"{airport['icao']:6} | {airport['name']} ({airport['country']})")

    @classmethod
    def export(cls, output_path: str | Path) -> Path:
        airports = cls.fetch()
        path = cls.save(airports, output_path)
        cls.preview(airports)
        return path
