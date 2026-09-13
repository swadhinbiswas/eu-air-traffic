"""Live-store bounds: forecast rows and growing flight sections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _forecast_payload() -> dict:
    # Times must be relative to now: _rows_for drops hours more than 1h old.
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    stamps = [(base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(3)]
    return {
        "current": {"time": stamps[0], "temperature_2m": 20.0, "weather_code": 1},
        "hourly": {
            "time": stamps,
            "temperature_2m": [20.0, 21.0, 22.0],
            "precipitation": [0.0, 0.0, 0.0],
            "wind_speed_10m": [3.0, 4.0, 5.0],
            "wind_direction_10m": [180.0, 190.0, 200.0],
            "weather_code": [1, 1, 2],
        },
    }


def test_forecast_keeps_every_hour_not_just_one() -> None:
    from services.live_store import LiveStore
    from services.sources.forecast import ForecastSource

    source = ForecastSource()
    rows = source._rows_for("EDDF", _forecast_payload())
    keys = [row[source.key] for row in rows]
    assert len(keys) == len(set(keys)), "station-hour keys must be unique"
    assert len(rows) == 4  # current + three hourly

    store = LiveStore()
    store.update("forecast", rows, source.key)
    assert len(store.section("forecast")) == 4


def test_flights_section_is_pruned_by_age() -> None:
    from services.live_store import LiveStore

    store = LiveStore()
    stale = (datetime.now(UTC) - timedelta(hours=13)).isoformat()
    fresh = datetime.now(UTC).isoformat()
    store.update(
        "flights",
        [
            {"flight_id": "OLD", "collected_at": stale},
            {"flight_id": "NEW", "collected_at": fresh},
        ],
        "flight_id",
    )
    ids = {row["flight_id"] for row in store.snapshot()["flights"]}
    assert ids == {"NEW"}


def test_health_is_degraded_without_live_positions() -> None:
    from services.live_store import LiveStore

    assert LiveStore().health()["status"] == "degraded"
