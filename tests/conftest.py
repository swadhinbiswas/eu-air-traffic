"""Shared pytest fixtures — isolate tests into a temp warehouse."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings


@pytest.fixture()
def test_settings(tmp_path: Path) -> Settings:
    """A Settings object pointing all storage into a throwaway directory."""
    warehouse = tmp_path / "warehouse"
    s = Settings(
        environment="test",
        mock_mode=True,
        warehouse_dir=warehouse,
        raw_dir=warehouse / "raw",
        bronze_dir=warehouse / "bronze",
        silver_dir=warehouse / "silver",
        gold_dir=warehouse / "gold",
        quarantine_dir=warehouse / "quarantine",
        checkpoint_dir=warehouse / "checkpoints",
        duckdb_path=warehouse / "air_traffic.duckdb",
        rate_limit_delay_seconds=0.0,
    )
    s.ensure_directories()
    return s
