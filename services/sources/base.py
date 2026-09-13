"""Source contract shared by every VPS collector source."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from config.settings import Settings, settings


class Source(ABC):
    """One data product polled from an upstream API.

    Implementations must never raise for a transient upstream failure: log and
    return an empty list so the collector loop keeps running 24/7. The ``name``
    selects both the Kafka topic (via ``settings.kafka_topics``) and the Bronze
    directory used by the sink.
    """

    name: str = "base"
    key: str = "id"
    # Where records land in the live store, when that differs from the source
    # name (AirLabs flights feed the flights section, not a separate one).
    store_section: str | None = None

    def __init__(self, app_settings: Settings | None = None) -> None:
        self.settings = app_settings or settings

    @abstractmethod
    def fetch(self) -> list[dict[str, Any]]:
        """Return normalised records (empty on failure)."""

    @property
    def topic(self) -> str:
        """Kafka topic this source publishes to."""
        return self.settings.topic_for_source(self.name)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{type(self).__name__} name={self.name!r} topic={self.topic!r}>"
