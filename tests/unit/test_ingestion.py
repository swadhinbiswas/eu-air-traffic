"""Unit tests for the ingestion base / utils layer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from config.settings import Settings
from ingestion.base import CheckpointStore, Collector
from ingestion.utils import atomic_write_json, iter_nested, to_float


class DummyCollector(Collector):
    name = "dummy"
    source = "dummy"

    def __init__(self, records: list[dict], app_settings: Settings | None = None):
        super().__init__(app_settings)
        self._records = records

    def fetch(self, start: datetime) -> list[dict]:
        return self._records


def test_checkpoint_roundtrip(test_settings: Settings):
    store = CheckpointStore(test_settings.checkpoint_dir)
    now = datetime.now(UTC)
    store.write("flights", now)
    assert store.read("flights") == now.astimezone(UTC)


def test_checkpoint_default_when_missing(test_settings: Settings):
    store = CheckpointStore(test_settings.checkpoint_dir)
    default = store.read("nope")
    assert default.tzinfo == UTC


def test_collector_writes_jsonl_and_advances_watermark(test_settings: Settings):
    records = [{"flight_id": "LH123", "delay": 5}]
    collector = DummyCollector(records, test_settings)
    count = collector.run()
    assert count == 1

    files = list(test_settings.bronze_dir.rglob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8")
    assert json.loads(line)["flight_id"] == "LH123"

    watermark = collector.checkpoints.read("dummy")
    assert watermark > datetime.now(UTC) - timedelta(minutes=5)


def test_collector_empty_is_noop(test_settings: Settings):
    collector = DummyCollector([], test_settings)
    assert collector.run() == 0
    assert list(test_settings.bronze_dir.rglob("*.jsonl")) == []


def test_atomic_write_json(tmp_path):
    target = tmp_path / "sub" / "out.json"
    atomic_write_json(target, {"a": [1, 2]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": [1, 2]}
    assert not list(tmp_path.glob("*.tmp"))


def test_iter_nested_finds_all_values():
    data = {"a": [{"b": 1}, {"b": 2}], "c": {"b": 3}}
    assert sorted(iter_nested(data, "b")) == [1, 2, 3]


def test_to_float():
    assert to_float("12.5") == 12.5
    assert to_float(None) is None
    assert to_float("garbage") is None
