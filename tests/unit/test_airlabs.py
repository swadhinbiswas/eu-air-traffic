"""AirLabs schedule source: parsing and the monthly call budget."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from config.settings import Settings

_SCHEDULE = {
    "response": [
        {
            "flight_iata": "LH100",
            "flight_icao": "DLH100",
            "dep_iata": "FRA",
            "dep_time_utc": "2026-09-13 06:30",
            "arr_iata": "LHR",
            "arr_time_utc": "2026-09-13 08:10",
            "status": "scheduled",
            "dep_delayed": 15,
            "arr_delayed": 25,
        },
        {
            "flight_iata": "BA902",
            "flight_icao": "BAW902",
            "dep_iata": "FRA",
            "dep_time_utc": "2026-09-13 07:00",
            "arr_iata": "LHR",
            "arr_time_utc": "2026-09-13 08:35",
            "status": "cancelled",
            "dep_delayed": None,
            "arr_delayed": None,
        },
    ]
}


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.calls = 0
        self.hubs: list[str | None] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        self.hubs.append(params.get("dep_icao") if params else None)
        return _FakeResponse(_SCHEDULE)


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "airlabs_api_key": "test-key",
        "airlabs_hubs_per_cycle": 2,
        "airlabs_monthly_budget": 5,
        "warehouse_dir": tmp_path,
    }
    values.update(overrides)
    return Settings(**values)


def test_airlabs_parses_schedules(tmp_path) -> None:
    from services.sources.airlabs import AirlabsSource

    session = _FakeSession()
    src = AirlabsSource(
        _settings(tmp_path, airlabs_hubs_per_cycle=1),
        session=session,
        state_path=tmp_path / "state.json",
    )
    rows = src.fetch()
    assert len(rows) == 2
    first = rows[0]
    assert first["departure_icao"] == "EDDF"  # queried hub is authoritative
    assert first["arrival_icao"] == "EGLL"  # LHR resolved from the bundled list
    assert first["callsign"] == "DLH100"
    assert first["airline_icao"] == "DLH"
    assert first["status"] == "scheduled"
    assert first["delay_minutes"] == 25.0
    assert first["scheduled_departure"] == "2026-09-13T06:30:00+00:00"
    assert rows[1]["status"] == "cancelled"
    assert rows[1]["cancelled"] is True


def test_airlabs_stops_at_the_monthly_budget(tmp_path) -> None:
    from services.sources.airlabs import AirlabsSource

    session = _FakeSession()
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"month": datetime.now(UTC).strftime("%Y-%m"), "calls": 5, "hub_index": 0})
    )
    src = AirlabsSource(_settings(tmp_path), session=session, state_path=state)
    assert src.fetch() == []
    assert session.calls == 0


def test_airlabs_rotates_hubs_and_persists_calls(tmp_path) -> None:
    from services.sources.airlabs import AirlabsSource

    session = _FakeSession()
    state = tmp_path / "state.json"
    src = AirlabsSource(_settings(tmp_path), session=session, state_path=state)
    src.fetch()
    assert session.hubs == ["EDDF", "EGLL"]
    saved = json.loads(state.read_text())
    assert saved["calls"] == 2
    assert saved["hub_index"] == 2

    src.fetch()
    assert session.hubs[2:] == ["LFPG", "EHAM"]


def test_airlabs_falls_back_to_iata_when_icao_query_is_empty(tmp_path) -> None:
    """A free key can reject dep_icao; the source must retry with dep_iata."""
    from services.sources.airlabs import AirlabsSource

    class _IcaoEmptySession:
        def __init__(self):
            self.params: list[dict] = []

        def get(self, url, params=None, headers=None, timeout=None):
            self.params.append(dict(params or {}))
            if "dep_icao" in params:
                return _FakeResponse({"response": []})
            return _FakeResponse(_SCHEDULE)

    session = _IcaoEmptySession()
    src = AirlabsSource(
        _settings(tmp_path, airlabs_hubs_per_cycle=1),
        session=session,
        state_path=tmp_path / "state.json",
    )
    rows = src.fetch()
    assert rows, "IATA fallback returned nothing"
    assert "dep_icao" in session.params[0]
    assert "dep_iata" in session.params[1]
    assert rows[0]["departure_icao"] == "EDDF"
