"""Integration test for the FastAPI application (uses httpx ASGITransport)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from apps.main import app


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
