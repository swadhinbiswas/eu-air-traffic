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
    store.update(
        "taf",
        [
            {
                "station_icao": "EDDF",
                "issue_time": "2026-01-01T00:00:00.000Z",
                "valid_from": 1767225600,
                "valid_to": 1767312000,
                "raw_taf": "TAF EDDF 010000Z ...",
            }
        ],
        "station_icao",
    )
    return store


def test_positions_are_enriched_with_emissions() -> None:
    from services.emissions import _openap

    store = _store()
    snapshot = store.snapshot()
    # The fixture flies an A320 at 32,000 ft / 450 kt: the OpenAP grid snaps
    # that to the 30,000 ft · level · 450 kt cell.
    kernel = _openap()["types"]["A320"]["flow"][1][6][3]
    assert snapshot["positions"][0]["fuel_burn_kg_per_hour"] == kernel
    assert snapshot["positions"][0]["co2_kg_per_hour"] == round(kernel * 2.52, 1)
    assert snapshot["emissions"]["total_co2_kg_per_hour"] == round(kernel * 2.52, 1)
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
    from services.emissions import _openap

    kernel = _openap()["types"]["A320"]["flow"][1][6][3]
    assert client.get("/live/emissions").json()["total_co2_kg_per_hour"] == round(kernel * 2.52, 1)
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

    import services.collector as collector_module
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

    # A freshly booted host: monotonic() is far below the publish interval.
    # Regression guard — the first poll must still be due.
    clock = {"t": 10.0}
    monkeypatch.setattr(collector_module.time, "monotonic", lambda: clock["t"])

    svc = CollectorService(sources=[Dummy()])
    assert svc.poll_once(svc.sources[0]) == 1, "first poll must publish immediately"
    assert svc.poll_once(svc.sources[0]) == 0, "second poll is inside the interval"
    assert svc.store.snapshot()["counts"]["positions"] == 1

    # Once the interval has elapsed it publishes again.
    clock["t"] += svc.settings.positions_publish_interval_seconds + 1
    assert svc.poll_once(svc.sources[0]) == 1


class _FlightSession:
    """OpenSky's dedicated endpoints, one record per direction."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None, params=None, auth=None):
        self.calls.append(url)
        if url.endswith("/flights/departure"):
            return _FakeResponse(
                payload=[
                    {
                        "icao24": "aaa111",
                        "firstSeen": 100,
                        "lastSeen": 200,
                        "callsign": "DLH1",
                        "estDepartureAirport": "EDDF",
                        "estArrivalAirport": None,
                    }
                ]
            )
        return _FakeResponse(
            payload=[
                {
                    "icao24": "bbb222",
                    "firstSeen": 300,
                    "lastSeen": 400,
                    "callsign": "DLH2",
                    "estDepartureAirport": "EGLL",
                    "estArrivalAirport": "EDDF",
                }
            ]
        )


def test_flights_source_queries_both_directions(monkeypatch) -> None:
    from services.sources.flights import FlightsSource

    session = _FlightSession()
    src = FlightsSource(session=session)
    monkeypatch.setattr(src.settings, "opensky_username", "user")
    monkeypatch.setattr(src.settings, "opensky_password", "pass")
    monkeypatch.setattr(src.settings, "flights_airports", 1)

    rows = src.fetch()
    assert any(url.endswith("/flights/all") for url in session.calls)
    assert any(url.endswith("/flights/departure") for url in session.calls)
    assert any(url.endswith("/flights/arrival") for url in session.calls)
    by_id = {row["flight_id"]: row for row in rows}
    assert by_id["aaa111_100"]["departure_icao"] == "EDDF"
    assert by_id["aaa111_100"]["arrival_icao"] is None
    assert by_id["aaa111_100"]["status"] == "en-route"
    # OpenSky has no schedule: delay unknown, never a fake zero.
    assert by_id["aaa111_100"]["delay_minutes"] is None
    assert by_id["bbb222_300"]["arrival_icao"] == "EDDF"
    assert by_id["bbb222_300"]["status"] == "landed"


def test_flights_source_falls_back_to_queried_airport(monkeypatch) -> None:
    from services.sources.flights import FlightsSource

    class _NoEstimate(_FlightSession):
        def get(self, url, headers=None, timeout=None, params=None, auth=None):
            self.calls.append(url)
            return _FakeResponse(
                payload=[
                    {
                        "icao24": "ccc333",
                        "firstSeen": 500,
                        "lastSeen": 600,
                        "callsign": "DLH3",
                        "estDepartureAirport": None,
                        "estArrivalAirport": None,
                    }
                ]
            )

    session = _NoEstimate()
    src = FlightsSource(session=session)
    monkeypatch.setattr(src.settings, "opensky_username", "user")
    monkeypatch.setattr(src.settings, "opensky_password", "pass")
    monkeypatch.setattr(src.settings, "flights_airports", 1)

    rows = src.fetch()
    # /flights/all row has no ends, the departure and arrival rows each supply
    # the airport that was queried.
    assert len(rows) == 3
    departure = next(r for r in rows if r["departure_icao"] == "EDDF")
    arrival = next(r for r in rows if r["arrival_icao"] == "EDDF")
    assert departure["arrival_icao"] is None
    assert arrival["departure_icao"] is None


def test_ground_vehicle_emissions_are_zero() -> None:
    from services.emissions import enrich_emissions

    for pseudo in ("GND", "TWR", "gnd"):
        row = enrich_emissions({"icao24": "X", "aircraft_type": pseudo})
        assert row["co2_kg_per_hour"] == 0.0
        assert row["fuel_burn_kg_per_hour"] == 0.0
        assert row["co2_estimated"] is False


def test_unknown_type_is_flagged_estimated() -> None:
    from services.emissions import enrich_emissions

    row = enrich_emissions({"icao24": "Y", "aircraft_type": "ZZZ9"})
    assert row["co2_kg_per_hour"] == 5040.0  # A320 fallback
    assert row["co2_estimated"] is True
    known = enrich_emissions({"icao24": "Z", "aircraft_type": "A320"})
    assert known["co2_estimated"] is False


def test_emissions_summary_splits_measured_and_estimated() -> None:
    store = LiveStore()
    store.update(
        "positions",
        [
            {
                "icao24": "a1",
                "aircraft_type": "A320",
                "latitude": 50.0,
                "longitude": 8.0,
                "collected_at": _now(),
            },
            {
                "icao24": "b2",
                "aircraft_type": None,
                "latitude": 51.0,
                "longitude": 9.0,
                "collected_at": _now(),
            },
            {
                "icao24": "c3",
                "aircraft_type": "GND",
                "latitude": 52.0,
                "longitude": 10.0,
                "collected_at": _now(),
            },
        ],
        "icao24",
    )
    summary = store.snapshot()["emissions"]
    assert summary["measured_aircraft"] == 2  # A320 + confident-zero GND
    assert summary["estimated_aircraft"] == 1  # only the unknown type
    assert summary["measured_co2_kg_per_hour"] == 5040.0
    assert summary["estimated_co2_kg_per_hour"] == 5040.0
    assert summary["total_co2_kg_per_hour"] == 10080.0


class _FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"ac": []}
        self.headers = headers or {}

    def json(self):
        return self._payload


class _FakeSession:
    """adsb.lol fails on every circle; OpenSky returns one row.

    airplanes.live is blocked (403), which is what this machine sees — the VPS
    may fare better, but the fallback chain must survive both failing.
    """

    def __init__(self, circle_status=500):
        self.circle_status = circle_status
        self.get_calls = 0
        self.adsb_calls = 0
        self.airplanes_calls = 0
        self.opensky_calls = 0

    def get(self, url, headers=None, timeout=None, params=None, auth=None):
        self.get_calls += 1
        if "adsb.lol" in url:
            self.adsb_calls += 1
            return _FakeResponse(status=self.circle_status)
        if "airplanes.live" in url:
            self.airplanes_calls += 1
            return _FakeResponse(status=403)
        self.opensky_calls += 1
        return _FakeResponse(
            payload={
                "states": [
                    [
                        "abc999",
                        "DLH1",
                        None,
                        None,
                        1,
                        8.0,
                        50.0,
                        9000,
                        False,
                        200,
                        90,
                        0,
                        None,
                        None,
                        "1000",
                        None,
                        None,
                    ],
                ]
            }
        )


def test_positions_merge_opensky_on_partial_adsb_failure() -> None:
    from services.sources.positions import PositionsSource

    session = _FakeSession(circle_status=500)
    src = PositionsSource(session=session)
    rows = src.fetch()
    assert len(rows) == 1
    assert rows[0]["icao24"] == "ABC999"
    assert rows[0]["source"] == "opensky"


def test_positions_429_parks_circle_in_cooldown() -> None:
    from services.sources.positions import PositionsSource

    session = _FakeSession(circle_status=429)
    src = PositionsSource(session=session)
    assert len(src.fetch()) == 1  # opensky fill on first degraded tick
    assert session.adsb_calls == 10
    assert len(src._cooldown_until) == 10
    # Second tick: adsb.lol circles are skipped, the airplanes.live fallback is
    # still tried (and blocked here), and OpenSky fills the gaps.
    session.adsb_calls = 0
    assert len(src.fetch()) == 1
    assert session.adsb_calls == 0
    assert session.opensky_calls >= 1


def test_taf_endpoint_returns_camel_case_contract() -> None:
    app = create_app(_store())
    client = TestClient(app)
    taf = client.get("/live/taf?ids=EDDF").json()["taf"]
    assert taf and taf[0]["icao"] == "EDDF"
    assert taf[0]["rawTAF"].startswith("TAF EDDF")
    assert "issueTime" in taf[0] and "raw_taf" not in taf[0]


def test_aircraft_endpoint_enriches_from_position() -> None:
    app = create_app(_store())
    client = TestClient(app)
    body = client.get("/live/aircraft/ABC123").json()
    assert body["found"] is True
    assert body["operator"] == "Lufthansa"  # resolved from the DLH callsign
    assert body["type"] == "A320"
    assert body["aircraftClass"] == "passenger"


def test_reference_reads_the_committed_network() -> None:
    """The reference loader must find services/data, not fall back to synthetic."""
    from ingestion import reference

    routes = reference.routes() or []
    assert len(routes) > 1_000, "real OpenFlights route network not loaded"
    assert (
        reference.route_distance_km("EDDF", "EGLL")
        and reference.route_distance_km("EDDF", "EGLL") > 600
    )


def test_collector_stop_flushes_exactly_once() -> None:
    """stop() used to return early because run() clears _running first."""
    from services.collector import CollectorService
    from services.sources.base import Source

    class _Noop(Source):
        name = "positions"
        key = "icao24"

        def fetch(self):
            return []

    svc = CollectorService(sources=[_Noop()])
    closed = {"count": 0}
    svc.bus.close = lambda: closed.__setitem__("count", closed["count"] + 1)  # type: ignore[method-assign]
    svc.stop()
    svc.stop()
    assert closed["count"] == 1


def test_airlabs_records_land_in_the_flights_section() -> None:
    from services.sources.airlabs import AirlabsSource

    assert AirlabsSource.store_section == "flights"
