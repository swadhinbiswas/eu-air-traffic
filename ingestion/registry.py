"""Collector registry — maps source names to collector factories.

The orchestrator and the FastAPI app use this single registry so new data
sources only need to register a collector here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ingestion.base import Collector

_REGISTRY: dict[str, Callable[..., Collector]] = {}


def register(name: str, factory: Callable[..., Collector]) -> None:
    """Register a collector under a canonical source name."""
    _REGISTRY[name] = factory


def available() -> list[str]:
    """List all registered source names (sorted for stable output)."""
    return sorted(_REGISTRY)


def create(name: str, app_settings: Any = None) -> Collector:
    """Instantiate the collector registered under ``name``."""
    try:
        if app_settings is not None:
            return _REGISTRY[name](app_settings=app_settings)
        return _REGISTRY[name]()
    except KeyError as exc:
        raise KeyError(f"Unknown collector source: {name!r}. Available: {available()}") from exc


def register_defaults() -> None:
    """Register all built-in collectors (idempotent)."""
    if _REGISTRY:
        return
    from ingestion.airports.collector import AirportCollector
    from ingestion.flights.collector import FlightCollector
    from ingestion.fuel.collector import FuelCollector
    from ingestion.holidays.collector import HolidayCollector
    from ingestion.weather.collector import WeatherCollector

    register("airports", AirportCollector)
    register("flights", FlightCollector)
    register("weather", WeatherCollector)
    register("holidays", HolidayCollector)
    register("fuel", FuelCollector)


register_defaults()
