"""Unit tests for new batch collectors: aircraft, routes, emissions, notams."""

from __future__ import annotations

from datetime import UTC, datetime

from config.settings import Settings
from ingestion.aircraft.collector import AircraftCollector
from ingestion.emissions.collector import EmissionsCollector
from ingestion.notams.collector import NotamCollector
from ingestion.routes.collector import RouteCollector


def test_aircraft_collector_fallback(test_settings: Settings):
    """Aircraft collector should return records from fallback data."""
    collector = AircraftCollector(test_settings)
    records = collector.fetch(datetime.now(UTC))
    assert len(records) > 0
    assert all("type_icao" in r for r in records)
    assert all("manufacturer" in r for r in records)
    assert all("capacity" in r for r in records)


def test_aircraft_collector_mock_mode(test_settings: Settings):
    """Aircraft collector should return mock data when enabled."""
    test_settings = Settings(mock_mode=True, environment="test")
    collector = AircraftCollector(test_settings)
    records = collector.fetch(datetime.now(UTC))
    assert len(records) > 0
    assert all("source" in r for r in records)


def test_routes_collector_fallback(test_settings: Settings):
    """Routes collector should return records from fallback data."""
    collector = RouteCollector(test_settings)
    records = collector.fetch(datetime.now(UTC))
    assert len(records) > 0
    assert all("origin" in r for r in records)
    assert all("destination" in r for r in records)
    assert all("origin" != r.get("destination") for r in records)


def test_emissions_collector_returns_data(test_settings: Settings):
    """Emissions collector should return emission factor data."""
    collector = EmissionsCollector(test_settings)
    records = collector.fetch(datetime.now(UTC))
    assert len(records) > 0
    assert all("aircraft_type" in r for r in records)
    assert all("co2_kg_per_hour" in r for r in records)
    assert all(r["co2_kg_per_hour"] > 0 for r in records)


def test_notams_collector_fallback(test_settings: Settings):
    """NOTAMs collector should return records from fallback data."""
    collector = NotamCollector(test_settings)
    records = collector.fetch(datetime.now(UTC))
    assert len(records) > 0
    assert all("notam_id" in r for r in records)
    assert all("icao_location" in r for r in records)
    assert all("notam_type" in r for r in records)


def test_notam_collector_classify_type():
    """NOTAM collector should classify types correctly."""
    collector = NotamCollector()
    assert collector._classify_notam("RWY CLOSED") == "runway_closure"
    assert collector._classify_notam("TWY CLSD") == "taxiway_closure"
    assert collector._classify_notam("ILS maintenance") == "navaid"
    assert collector._classify_notam("CRANE erected") == "obstacle"
    assert collector._classify_notam("BIRD flock") == "bird_activity"
    assert collector._classify_notam("SNOW on runway") == "weather"
    assert collector._classify_notam("RESTRICTED airspace") == "airspace"
    assert collector._classify_notam("unknown thing") == "other"
