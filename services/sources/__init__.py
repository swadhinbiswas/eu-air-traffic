"""VPS collector sources.

Each module exposes a :class:`~services.sources.base.Source` that fetches one
data product from an upstream aviation API and returns normalised records ready
for Kafka and the live snapshot. Source names match the Kafka topic keys in
:attr:`config.settings.Settings.kafka_topics`.
"""

from __future__ import annotations

from services.sources.base import Source
from services.sources.flights import FlightsSource
from services.sources.forecast import ForecastSource
from services.sources.fuel import FuelSource
from services.sources.positions import PositionsSource
from services.sources.reference import ReferenceSource
from services.sources.weather import MetarSource, TafSource

__all__ = [
    "Source",
    "PositionsSource",
    "FlightsSource",
    "MetarSource",
    "TafSource",
    "ForecastSource",
    "FuelSource",
    "ReferenceSource",
    "build_sources",
]


def build_sources(app_settings=None) -> list[Source]:
    """Instantiate the full VPS collector source set."""
    return [
        PositionsSource(app_settings),
        FlightsSource(app_settings),
        MetarSource(app_settings),
        TafSource(app_settings),
        ForecastSource(app_settings),
        FuelSource(app_settings),
        ReferenceSource(app_settings),
    ]
