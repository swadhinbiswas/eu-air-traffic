"""Integration test — full Medallion pipeline against a temp warehouse."""

from __future__ import annotations

import duckdb

from config.settings import Settings
from pipelines.orchestrator import Orchestrator


def _settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        mock_mode=True,
        warehouse_dir=tmp_path / "warehouse",
        raw_dir=tmp_path / "warehouse" / "raw",
        bronze_dir=tmp_path / "warehouse" / "bronze",
        silver_dir=tmp_path / "warehouse" / "silver",
        gold_dir=tmp_path / "warehouse" / "gold",
        quarantine_dir=tmp_path / "warehouse" / "quarantine",
        checkpoint_dir=tmp_path / "warehouse" / "checkpoints",
        duckdb_path=tmp_path / "warehouse" / "air_traffic.duckdb",
        rate_limit_delay_seconds=0.0,
    )


def test_end_to_end_pipeline(tmp_path):
    s = _settings(tmp_path)
    s.ensure_directories()

    report = Orchestrator(s).run()

    assert report.success, f"pipeline failed: {report.errors}"
    assert report.steps["collect"]["status"] == "ok"
    assert report.steps["warehouse"]["status"] == "ok"

    # Bronze JSONL written for every source.
    jsonl_count = sum(1 for _ in (s.bronze_dir / "flights").rglob("*.jsonl"))
    assert jsonl_count >= 1

    # Silver parquet for flights.
    assert (s.silver_dir / "flights" / "data.parquet").exists()

    # Gold marts produced.
    assert (s.gold_dir / "airport_metrics" / "airport_metrics.parquet").exists()

    # DuckDB has fact_flights + dims populated.
    con = duckdb.connect(str(s.duckdb_path), read_only=True)
    n = con.execute("SELECT COUNT(*) FROM fact_flights").fetchone()[0]
    assert n > 0
    con.close()


def test_pipeline_is_idempotent(tmp_path):
    s = _settings(tmp_path)
    s.ensure_directories()

    Orchestrator(s).run()
    first = (
        duckdb.connect(str(s.duckdb_path), read_only=True)
        .execute("SELECT COUNT(*) FROM fact_flights")
        .fetchone()[0]
    )

    Orchestrator(s).run()
    second = (
        duckdb.connect(str(s.duckdb_path), read_only=True)
        .execute("SELECT COUNT(*) FROM fact_flights")
        .fetchone()[0]
    )

    # Synthetic data is deterministic per day → fact table size stable.
    assert first == second
