"""Unit tests for the VPS collector spine: live store, live API and Kafka sink."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from services.live_api import create_app
from services.live_store import LiveStore
from services.sink import KafkaSink


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _store() -> LiveStore:
    store = LiveStore()
    store.update_reference(
        [
            {"_kind": "airport", "id": "airport:EDDF", "icao": "EDDF", "name": "Frankfurt"},
            {"_kind": "route", "id": "route:1", "origin": "EDDF", "destination": "EGLL"},
            {
                "_kind": "emission",
                "id": "emission:A320",
                "aircraft_type": "A320",
                "co2_kg_per_hour": 5040.0,
            },
        ]
    )
    store.update(
        "positions",
        [
            {
                "icao24": "abc123",
                "callsign": "DLH1",
                "aircraft_type": "A320",
                "latitude": 50.0,
                "longitude": 8.0,
                "velocity": 450,
                "altitude": 32000,
                "collected_at": _now(),
            }
        ],
        "icao24",
    )
    store.update(
        "metar",
        [
            {
                "station_icao": "EDDF",
                "timestamp": _now(),
                "temperature_c": 18.0,
                "latitude": 50.0,
                "longitude": 8.0,
                "flight_category": "VFR",
            }
        ],
        "station_icao",
    )
    return store


def test_positions_are_enriched_with_emissions() -> None:
    store = _store()
    snapshot = store.snapshot()
    assert snapshot["positions"][0]["co2_kg_per_hour"] == 5040.0
    assert snapshot["positions"][0]["fuel_burn_kg_per_hour"] > 0
    assert snapshot["emissions"]["total_co2_kg_per_hour"] == 5040.0
    assert snapshot["counts"]["airports"] == 1
    assert snapshot["counts"]["routes"] == 1


def test_reference_split_by_kind() -> None:
    store = _store()
    snap = store.snapshot()
    assert snap["reference"]["airports"][0]["icao"] == "EDDF"
    assert snap["reference"]["routes"][0]["origin"] == "EDDF"
    assert snap["reference"]["emission_factors"][0]["aircraft_type"] == "A320"


def test_stale_positions_are_pruned() -> None:
    store = LiveStore()
    store.update(
        "positions",
        [
            {
                "icao24": "old1",
                "latitude": 1.0,
                "longitude": 1.0,
                "collected_at": "2020-01-01T00:00:00+00:00",
            }
        ],
        "icao24",
    )
    assert store.snapshot()["counts"]["positions"] == 0


def test_live_api_endpoints() -> None:
    app = create_app(_store())
    client = TestClient(app)
    assert client.get("/health").status_code == 200

    snapshot = client.get("/live/snapshot").json()
    assert snapshot["counts"]["positions"] == 1

    assert client.get("/live/positions").json()["count"] == 1
    assert client.get("/live/weather").json()["metar"][0]["station_icao"] == "EDDF"
    assert client.get("/live/emissions").json()["total_co2_kg_per_hour"] == 5040.0
    assert client.get("/live/reference/airports").json()["airports"][0]["icao"] == "EDDF"
    assert client.get("/live/aircraft/ABC123").json()["found"] is True
    assert client.get("/live/aircraft/ZZZZZZ").json()["found"] is False
    assert client.get("/live/reference/nope").status_code == 404


def test_sink_dataset_mapping() -> None:
    sink = KafkaSink()
    topics = sink.settings.kafka_topics
    assert sink._datasets_for(topics["weather"], {"_kind": "metar"}) == ["weather"]
    assert sink._datasets_for(topics["weather"], {"_kind": "forecast"}) == ["weather_forecast"]
    assert sink._datasets_for(topics["weather"], {"_kind": "taf"}) == ["weather_taf"]
    assert sink._datasets_for(topics["weather"], {}) == ["weather"]
    assert sink._datasets_for(topics["positions"], {}) == ["positions"]
    assert sink._datasets_for(topics["flights"], {}) == ["flights"]
    assert sink._datasets_for(topics["fuel"], {}) == ["fuel"]
    assert sink._datasets_for(topics["reference"], {"_kind": "route"}) == ["routes"]
    assert sink._datasets_for(topics["reference"], {"_kind": "emission"}) == ["emissions"]


def test_sink_legacy_topics_drain_to_same_datasets() -> None:
    """Pre-``_kind`` records from the retired topics route by record shape."""
    sink = KafkaSink()
    assert sink._datasets_for("eu-metar", {"station_icao": "EDDF"}) == ["weather"]
    assert sink._datasets_for("eu-taf", {"raw_taf": "TAF EDDF"}) == ["weather_taf"]
    assert sink._datasets_for("eu-forecast", {"is_forecast": True}) == ["weather_forecast"]
    assert sink._datasets_for("eu-collect-meta", {}) == []


def test_five_topics_only() -> None:
    from config.settings import settings

    assert sorted(settings.kafka_topics) == ["flights", "fuel", "positions", "reference", "weather"]


def test_sink_writes_jsonl_and_parquet(tmp_path, monkeypatch) -> None:
    from config.settings import settings

    monkeypatch.setattr(settings, "bronze_dir", tmp_path / "bronze")
    sink = KafkaSink(upload=False)
    rows = [{"icao24": "ABC", "latitude": 1.0, "longitude": 2.0}]
    records = sink._write_jsonl("positions", rows)
    parquet = sink._write_parquet("positions", rows)
    assert records.exists() and records.suffix == ".jsonl"
    assert parquet is not None and parquet.exists()
    import polars as pl

    assert pl.read_parquet(parquet).height == 1


def test_reference_ids_are_unique_per_kind() -> None:
    """Routes/aircraft/holidays share no identifier — ids must not collapse."""
    from services.sources.reference import ReferenceSource

    rows = ReferenceSource().fetch()
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    kinds = {row["_kind"] for row in rows}
    assert {"airport", "route", "aircraft", "holiday", "emission"} <= kinds


def test_collector_publishes_positions_on_a_slower_cadence(monkeypatch) -> None:
    """Positions update the live store every tick but hit Kafka rarely."""
    from datetime import UTC, datetime

    from services.collector import CollectorService
    from services.sources.base import Source

    class Dummy(Source):
        name = "positions"
        key = "icao24"

        def fetch(self):
            return [
                {
                    "icao24": "ABC",
                    "aircraft_type": "A320",
                    "latitude": 50.0,
                    "longitude": 8.0,
                    "collected_at": datetime.now(UTC).isoformat(),
                }
            ]

    svc = CollectorService(sources=[Dummy()])
    # First poll is due immediately; the next one is not.
    assert svc.poll_once(svc.sources[0]) == 1
    assert svc.poll_once(svc.sources[0]) == 0
    assert svc.store.snapshot()["counts"]["positions"] == 1
