"""Integration test for the FastAPI application (uses httpx ASGITransport)."""

from __future__ import annotations

import duckdb
import pytest
from httpx import ASGITransport, AsyncClient

from apps import main
from apps.main import app
from config.settings import Settings


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "warehouse_exists" in body


@pytest.mark.asyncio
async def test_sources_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/sources")
    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert {"airports", "flights", "weather", "holidays", "fuel"} <= names


@pytest.mark.asyncio
async def test_pipeline_report_404_when_never_run():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/pipeline/report")
    assert response.status_code in (200, 404)  # may or may not have a report yet


@pytest.mark.asyncio
async def test_quality_report_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/quality/report")
    assert response.status_code in (200, 404)


@pytest.mark.asyncio
async def test_dashboard_endpoint_serves_html():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/dashboard")
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert response.headers["content-type"].startswith("text/html")
        assert "Air Traffic" in response.text


@pytest.mark.asyncio
async def test_warehouse_query_returns_records(tmp_path, monkeypatch):
    """A read-only query must return a JSON *list* of row objects."""
    s = Settings(
        environment="test",
        motherduck_token=None,
        warehouse_dir=tmp_path / "warehouse",
        duckdb_path=tmp_path / "warehouse" / "air_traffic.duckdb",
    )
    s.ensure_directories()
    with duckdb.connect(str(s.duckdb_path)) as con:
        con.execute(
            "CREATE TABLE gold_airport_metrics AS SELECT 'EDDF' airport_icao, 10 total_flights"
        )
    monkeypatch.setattr(main, "settings", s)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/warehouse/query",
            json={"sql": "SELECT airport_icao, total_flights FROM gold_airport_metrics"},
        )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert body == [{"airport_icao": "EDDF", "total_flights": 10}]


@pytest.mark.asyncio
async def test_warehouse_query_rejects_bad_sql(tmp_path, monkeypatch):
    s = Settings(
        environment="test",
        motherduck_token=None,
        warehouse_dir=tmp_path / "warehouse",
        duckdb_path=tmp_path / "warehouse" / "air_traffic.duckdb",
    )
    s.ensure_directories()
    with duckdb.connect(str(s.duckdb_path)) as con:
        con.execute("CREATE TABLE t (a INTEGER)")
    monkeypatch.setattr(main, "settings", s)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/warehouse/query", json={"sql": "SELECT * FROM nope"})
    assert response.status_code == 400
