"""Unit tests for the tooling scripts (dashboard generator + HF upload)."""

from __future__ import annotations

import duckdb
import polars as pl

from config.settings import Settings
from scripts import upload_hf
from scripts.build_dashboard import build_dashboard, collect_data


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


def _seed_warehouse(s: Settings) -> None:
    s.ensure_directories()
    with duckdb.connect(str(s.duckdb_path)) as con:
        con.execute("CREATE TABLE dim_date (date DATE)")
        con.execute("CREATE TABLE dim_airport (airport_icao VARCHAR)")
        con.execute("CREATE TABLE dim_airline (airline_icao VARCHAR)")
        con.execute(
            "CREATE TABLE fact_flights (flight_id VARCHAR, status VARCHAR, delay_minutes DOUBLE)"
        )
        con.execute(
            "CREATE TABLE gold_airport_metrics AS SELECT 'EDDF' airport_icao, 10 total_flights, "
            "5.0 avg_delay_minutes, 0.9 on_time_rate"
        )
        con.execute(
            "CREATE TABLE gold_airline_rankings AS SELECT 'LH' airline_icao, 'Lufthansa' airline_name, "
            "10 total_flights, 4.0 avg_delay_minutes, 0.95 on_time_rate"
        )
        con.execute(
            "CREATE TABLE gold_delay_analysis AS SELECT 'scheduled' status, 5 flight_count, 0.0 avg_delay_minutes"
        )
        con.execute(
            "CREATE TABLE gold_weather_impact AS SELECT 'Clear' weather_condition, 3 flight_count, "
            "2.0 avg_delay_minutes, 12.0 avg_temperature_c"
        )
        con.execute(
            "CREATE TABLE gold_seasonal_trends AS SELECT '2026-01-01' flight_date, 12 hour_of_day, "
            "4 flight_count, 1.0 avg_delay_minutes"
        )
        con.execute(
            "CREATE TABLE gold_fuel_price_series AS SELECT '2026-01-01' date, 'EU' region, 1.5 price_per_litre"
        )
        con.execute(
            "INSERT INTO fact_flights VALUES ('F1', 'scheduled', 0.0), ('F2', 'cancelled', 0.0)"
        )
        con.execute("INSERT INTO dim_date VALUES ('2026-01-01'), ('2026-01-02')")
        con.execute("INSERT INTO dim_airport VALUES ('EDDF'), ('EGLL')")
        con.execute("INSERT INTO dim_airline VALUES ('LH')")


def test_build_dashboard_generates_single_file(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    _seed_warehouse(s)
    monkeypatch.setattr("scripts.build_dashboard.settings", s)

    out = tmp_path / "dashboard.html"
    build_dashboard(out)

    html = out.read_text(encoding="utf-8")
    assert "Air Traffic Analytics" in html
    assert '"total_flights"' in html
    assert '"airports": 2' in html
    assert "const DASH = {" in html


def test_build_dashboard_error_when_no_warehouse(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    s.ensure_directories()
    monkeypatch.setattr("scripts.build_dashboard.settings", s)

    data = collect_data()
    assert "error" in data


def test_upload_hf_skips_without_token(monkeypatch):
    class _FakeSettings:
        huggingface_token = ""
        huggingface_repo = "x/y"
        silver_dir = upload_hf.settings.silver_dir
        gold_dir = upload_hf.settings.gold_dir
        duckdb_path = upload_hf.settings.duckdb_path

    monkeypatch.setattr(upload_hf, "settings", _FakeSettings())
    result = upload_hf.upload_warehouse()
    assert result == {"skipped": True, "reason": "HF_TOKEN not set"}


def test_upload_hf_plan_uploads(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    s.ensure_directories()
    (s.silver_dir / "flights").mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"flight_id": ["F1"]}).write_parquet(s.silver_dir / "flights" / "data.parquet")
    (s.gold_dir / "airport_metrics").mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"airport_icao": ["EDDF"]}).write_parquet(
        s.gold_dir / "airport_metrics" / "airport_metrics.parquet"
    )

    monkeypatch.setattr(upload_hf, "settings", s)
    plan = upload_hf._plan_uploads()
    paths = {str(local) for local, _ in plan}
    assert any("data.parquet" in p for p in paths)
    assert any("airport_metrics.parquet" in p for p in paths)
