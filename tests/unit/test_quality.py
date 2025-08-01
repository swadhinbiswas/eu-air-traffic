"""Unit tests for the Data Quality framework."""

from __future__ import annotations

import json

from config.settings import Settings
from pipelines.quality import quality_report


def _settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        warehouse_dir=tmp_path / "warehouse",
        raw_dir=tmp_path / "warehouse" / "raw",
        bronze_dir=tmp_path / "warehouse" / "bronze",
        silver_dir=tmp_path / "warehouse" / "silver",
        gold_dir=tmp_path / "warehouse" / "gold",
        quarantine_dir=tmp_path / "warehouse" / "quarantine",
        checkpoint_dir=tmp_path / "warehouse" / "checkpoints",
        duckdb_path=tmp_path / "warehouse" / "air_traffic.duckdb",
    )


def test_quality_report_empty_warehouse(tmp_path):
    s = _settings(tmp_path)
    s.ensure_directories()

    report = quality_report(s)

    assert report["environment"] == "test"
    assert report["summary"]["bronze_rows"] == 0
    assert report["summary"]["overall_pass_rate"] is None
    assert report["summary"]["sources_with_data"] == 0
    for source in ("airports", "flights", "weather", "holidays", "fuel"):
        assert source in report["sources"]


def test_quality_report_counts_pass_and_quarantine(tmp_path):
    import polars as pl

    s = _settings(tmp_path)
    s.ensure_directories()

    # 3 valid flights → silver, 1 bad flight → quarantine
    flights = pl.DataFrame(
        {
            "flight_id": ["LH100", "LH101", "LH102"],
            "departure_icao": ["EDDF", "EGLL", "LFPG"],
            "arrival_icao": ["EGLL", "LFPG", "EDDF"],
            "scheduled_departure": ["2026-01-01T10:00:00+00:00"] * 3,
            "ingestion_date": ["2026-01-01"] * 3,
        }
    )
    bad = pl.DataFrame(
        {
            "flight_id": [None],
            "departure_icao": ["EDDF"],
            "arrival_icao": ["EDDF"],
            "scheduled_departure": ["2026-01-01T10:00:00+00:00"],
            "quarantine_reason": ["invalid keys"],
        }
    )
    (s.silver_dir / "flights").mkdir(parents=True)
    (s.quarantine_dir / "flights").mkdir(parents=True)
    flights.write_parquet(s.silver_dir / "flights" / "data.parquet")
    bad.write_parquet(s.quarantine_dir / "flights" / "quarantine_2026.parquet")

    report = quality_report(s)

    fl = report["sources"]["flights"]
    assert fl["silver_rows"] == 3
    assert fl["quarantined_rows"] == 1
    assert fl["pass_rate"] == 0.75
    assert report["summary"]["overall_pass_rate"] == 0.75


def test_quality_report_persists_json(tmp_path):
    s = _settings(tmp_path)
    s.ensure_directories()

    quality_report(s)

    path = s.checkpoint_dir / "quality_report.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "generated_at" in payload
    assert "summary" in payload
    assert "sources" in payload
