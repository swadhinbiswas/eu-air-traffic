"""FastAPI ingestion & analytics service.

Endpoints:
- ``GET  /health``            — liveness + storage readiness probe
- ``GET  /sources``           — registered data sources + credential status
- ``POST /ingest/{source}``   — run a single collector
- ``POST /ingest``            — run all collectors
- ``POST /pipeline/run``      — full ETL (collect → bronze → … → warehouse → quality)
- ``GET  /pipeline/report``   — last pipeline run report
- ``GET  /quality/report``    — last data-quality report
- ``POST /quality/check``     — run the data-quality scan now
- ``GET  /warehouse/tables``  — DuckDB table inventory + row counts
- ``GET  /warehouse/query``   — read-only SQL query against the warehouse
- ``GET  /kpis``              — headline business KPIs from the Gold layer
- ``GET  /dashboard``         — offline, self-contained HTML analytics dashboard

Run locally with: ``uvicorn apps.main:app --reload``
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from config.logging import logger
from config.settings import settings


# ── Request/response models ─────────────────────────────────────────────────
class IngestResponse(BaseModel):
    source: str
    records: int
    status: Literal["ok", "error"]
    detail: str = ""


class PipelineRunResponse(BaseModel):
    started_at: str
    finished_at: str | None = None
    elapsed_seconds: float = 0.0
    success: bool
    steps: dict[str, Any] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, description="Read-only SQL statement")


# ── Lifespan ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("air-traffic platform API starting (environment=%s)", settings.environment)
    settings.ensure_directories()
    yield
    logger.info("air-traffic platform API shutting down")


app = FastAPI(
    title="Air Traffic Analytics Platform",
    description=(
        "Ingestion API and analytics query surface for the Air Traffic "
        "Platform (FastAPI + Polars + DuckDB + dbt)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _run_in_executor(fn: Any) -> Any:
    loop = asyncio.get_event_loop()
    return asyncio.ensure_future(loop.run_in_executor(None, fn))


def _collector(name: str):
    from ingestion.registry import create

    try:
        return create(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _read_only_con() -> duckdb.DuckDBPyConnection:
    """Connection for read-only analytics — MotherDuck when configured, else local."""
    if settings.motherduck_enabled:
        try:
            return duckdb.connect(settings.serving_connection)
        except Exception as exc:  # noqa: BLE001 - fall back to the local file
            logger.warning("[api] MotherDuck connection failed, using local warehouse: %s", exc)
    if not settings.duckdb_path.exists():
        raise HTTPException(
            status_code=503, detail="Warehouse not built yet. Run POST /pipeline/run first."
        )
    return duckdb.connect(str(settings.duckdb_path), read_only=True)


# ── Routes ──────────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root() -> dict[str, Any]:
    return {
        "service": "air-traffic-platform",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "mock_mode": settings.mock_mode,
        "warehouse_exists": settings.duckdb_path.exists(),
        "data_sources": settings.credentials_available,
    }


@app.get("/sources")
async def sources() -> list[dict[str, Any]]:
    from ingestion.registry import available, create

    return [
        {
            "name": name,
            "collector": type(create(name)).__name__,
        }
        for name in available()
    ]


@app.post("/ingest/{source}", response_model=IngestResponse)
async def ingest(source: str) -> IngestResponse:
    collector = _collector(source)
    try:
        count = await _run_in_executor(collector.run)
    except Exception as exc:  # noqa: BLE001
        logger.error("[api] ingest %s failed: %s", source, exc)
        return IngestResponse(source=source, records=0, status="error", detail=str(exc))
    return IngestResponse(source=source, records=count, status="ok")


@app.post("/ingest")
async def ingest_all() -> dict[str, IngestResponse]:
    from ingestion.registry import available

    results: dict[str, IngestResponse] = {}
    for name in available():
        collector = _collector(name)
        try:
            count = await _run_in_executor(collector.run)
            results[name] = IngestResponse(source=name, records=count, status="ok")
        except Exception as exc:  # noqa: BLE001
            logger.error("[api] ingest %s failed: %s", name, exc)
            results[name] = IngestResponse(source=name, records=0, status="error", detail=str(exc))
    return results


@app.post("/pipeline/run", response_model=PipelineRunResponse)
async def run_pipeline() -> PipelineRunResponse:
    from pipelines.orchestrator import Orchestrator

    def _run() -> PipelineRunResponse:
        report = Orchestrator().run()
        return PipelineRunResponse(**report.to_dict())

    return await _run_in_executor(_run)


@app.get("/pipeline/report")
async def pipeline_report() -> dict[str, Any]:
    path = settings.checkpoint_dir / "pipeline_report.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No pipeline run recorded yet.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/quality/check")
async def quality_check() -> dict[str, Any]:
    """Run the data-quality scan now and return the fresh report."""
    from pipelines.quality import quality_report

    return await _run_in_executor(quality_report)


@app.get("/quality/report")
async def quality_report_endpoint() -> dict[str, Any]:
    from pipelines.quality import load_report

    report = load_report()
    if report is None:
        raise HTTPException(
            status_code=404, detail="No quality report yet. Run POST /pipeline/run first."
        )
    return report


@app.get("/warehouse/tables")
async def warehouse_tables() -> dict[str, Any]:
    con = _read_only_con()
    try:
        tables = con.execute(
            "SELECT table_name, table_type FROM information_schema.tables ORDER BY table_name"
        ).fetchall()
        rows: dict[str, int] = {}
        for name, _type in tables:
            try:
                count_row = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
                count = int(count_row[0]) if count_row is not None else 0
            except duckdb.Error:
                count = 0
            rows[name] = int(count)
        return rows
    finally:
        con.close()


@app.post("/warehouse/query")
async def warehouse_query(payload: QueryRequest) -> list[dict[str, Any]]:
    con = _read_only_con()
    try:
        return con.execute(payload.sql).fetch_df().to_dict(orient="records")
    except duckdb.Error as exc:
        raise HTTPException(status_code=400, detail=f"Query error: {exc}") from exc
    finally:
        con.close()


@app.get("/kpis")
async def kpis() -> dict[str, Any]:
    con = _read_only_con()
    try:
        kpis = con.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM fact_flights) AS total_flights,
                (SELECT AVG(delay_minutes) FROM fact_flights WHERE status != 'cancelled') AS avg_delay_minutes,
                (SELECT COUNT(*) FROM fact_flights WHERE status = 'cancelled') * 1.0 /
                    NULLIF((SELECT COUNT(*) FROM fact_flights), 0) AS cancellation_rate,
                (SELECT COUNT(*) FROM dim_airport) AS airports,
                (SELECT COUNT(*) FROM dim_airline) AS airlines
            """
        ).fetchone()
        columns = [
            "total_flights",
            "avg_delay_minutes",
            "cancellation_rate",
            "airports",
            "airlines",
        ]
        return dict(zip(columns, kpis or (), strict=False))
    finally:
        con.close()


def _dashboard_path() -> Path:
    from config.settings import settings as s

    return s.warehouse_dir / "dashboard.html"


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Serve the self-contained analytics dashboard (regenerates if stale/missing)."""
    path = _dashboard_path()
    if not path.exists():
        from scripts.build_dashboard import build_dashboard

        await _run_in_executor(build_dashboard)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.post("/dashboard/refresh")
async def dashboard_refresh() -> dict[str, Any]:
    """Regenerate the dashboard HTML from the current warehouse."""
    from scripts.build_dashboard import build_dashboard

    path = await _run_in_executor(build_dashboard)
    return {"status": "ok", "path": str(path)}


# ── Enhanced analytics endpoints ───────────────────────────────────────────────
@app.get("/api/v1/aircraft/fleet")
async def aircraft_fleet() -> list[dict[str, Any]]:
    """Get aircraft fleet data with utilization metrics."""
    con = _read_only_con()
    try:
        return (
            con.execute(
                """
            SELECT
                a.type_icao,
                a.manufacturer,
                a.family,
                a.engine,
                a.capacity,
                a.range_km,
                COUNT(DISTINCT r.origin || '-' || r.destination) AS routes_served,
                MAX(e.co2_kg_per_hour) AS co2_kg_per_hour
            FROM dim_aircraft a
            LEFT JOIN dim_route r ON r.equipment = a.type_icao
            LEFT JOIN fact_emissions e ON e.aircraft_type = a.type_icao
            GROUP BY a.type_icao, a.manufacturer, a.family, a.engine, a.capacity, a.range_km
            ORDER BY routes_served DESC
            """
            )
            .fetch_df()
            .to_dict(orient="records")
        )
    finally:
        con.close()


@app.get("/api/v1/routes/network")
async def routes_network(limit: int = 500) -> list[dict[str, Any]]:
    """Get route network with performance metrics."""
    con = _read_only_con()
    try:
        return (
            con.execute(
                """
            SELECT
                r.origin,
                r.destination,
                r.airline,
                r.stops,
                r.equipment,
                r.distance_km,
                COUNT(f.flight_id) as total_flights,
                AVG(f.delay_minutes) as avg_delay_minutes
            FROM dim_route r
            LEFT JOIN fact_flights f ON f.departure_icao = r.origin AND f.arrival_icao = r.destination
            GROUP BY r.origin, r.destination, r.airline, r.stops, r.equipment, r.distance_km
            ORDER BY total_flights DESC
            LIMIT ?
            """,
                (limit,),
            )
            .fetch_df()
            .to_dict(orient="records")
        )
    finally:
        con.close()


@app.get("/api/v1/emissions/summary")
async def emissions_summary() -> dict[str, Any]:
    """Get emissions summary statistics."""
    con = _read_only_con()
    try:
        result = con.execute(
            """
            SELECT
                COUNT(DISTINCT aircraft_type) as aircraft_types_monitored,
                SUM(co2_kg_per_hour) as total_co2_per_hour,
                AVG(co2_kg_per_hour) as avg_co2_per_hour,
                SUM(fuel_burn_liters_per_hour) as total_fuel_burn_per_hour
            FROM fact_emissions
            """
        ).fetchone()
        columns = [
            "aircraft_types_monitored",
            "total_co2_per_hour",
            "avg_co2_per_hour",
            "total_fuel_burn_per_hour",
        ]
        return dict(zip(columns, result or (), strict=False))
    finally:
        con.close()


@app.get("/api/v1/delays/current")
async def delays_current() -> list[dict[str, Any]]:
    """Get current delay statistics by airport."""
    con = _read_only_con()
    try:
        return (
            con.execute(
                """
            SELECT
                departure_icao as airport_icao,
                COUNT(*) as total_flights,
                AVG(delay_minutes) as avg_delay_minutes,
                MAX(delay_minutes) as max_delay_minutes,
                SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled_flights
            FROM fact_flights
            WHERE scheduled_departure > now() - INTERVAL '6 hours'
            GROUP BY departure_icao
            ORDER BY avg_delay_minutes DESC
            """
            )
            .fetch_df()
            .to_dict(orient="records")
        )
    finally:
        con.close()


@app.get("/api/v1/sectors/loading")
async def sectors_loading() -> list[dict[str, Any]]:
    """Get airspace sector loading (departure airport activity)."""
    con = _read_only_con()
    try:
        return (
            con.execute(
                """
            SELECT
                departure_icao as sector_code,
                CAST(scheduled_departure AS DATE) as date,
                COUNT(*) as flight_count,
                AVG(delay_minutes) as avg_delay_minutes
            FROM fact_flights
            WHERE scheduled_departure IS NOT NULL
            GROUP BY departure_icao, CAST(scheduled_departure AS DATE)
            ORDER BY date DESC, flight_count DESC
            LIMIT 100
            """
            )
            .fetch_df()
            .to_dict(orient="records")
        )
    finally:
        con.close()


@app.get("/api/v1/dbt/manifest")
async def dbt_manifest() -> dict[str, Any]:
    """Get dbt manifest information for lineage and documentation."""
    manifest_path = settings.dbt_project_dir / "target" / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(
            status_code=404,
            detail="dbt manifest not found. Run 'dbt compile' to generate it.",
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("apps.main:app", host="0.0.0.0", port=8000, reload=False)


def entrypoint() -> None:
    """Console-script entrypoint (``air-traffic-api``)."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
