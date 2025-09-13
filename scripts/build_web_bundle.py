"""Build the static web data bundle consumed by the God's Eye dashboard.

Reads the batch warehouse (``warehouse/air_traffic.duckdb``), the real-time
store (``warehouse/realtime.duckdb``), the EU airport reference file and the
dbt model graph, then writes compact JSON under ``web/public/data/``.

The bundle lets the React frontend render instantly with **no backend** and
deploy to any static host, while the live FastAPI service can still overlay
fresher data when reachable (hybrid mode).

Run with: ``python -m scripts.build_web_bundle``
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

try:  # PyYAML ships with dbt but is optional for the bundle
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

from config.logging import logger
from config.settings import PROJECT_ROOT, Settings, settings

BUNDLE_VERSION = 2
AIRPORT_REFERENCE = PROJECT_ROOT / "ingestion" / "airports" / "europe_airports.json"


# ── helpers ─────────────────────────────────────────────────────────────────
def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    """Run a query and return JSON-safe records (timestamps → ISO, NaN → null)."""
    df = con.execute(sql).fetch_df()
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, default: Any = None) -> Any:
    row = con.execute(sql).fetchone()
    return row[0] if row and row[0] is not None else default


def _pct(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(value * 100, digits)


def _write(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), default=str, allow_nan=False)
    path.write_text(text, encoding="utf-8")
    return len(text)


def _connect(path: Path, read_only: bool = True) -> duckdb.DuckDBPyConnection | None:
    if not path.exists():
        return None
    try:
        return duckdb.connect(str(path), read_only=read_only)
    except duckdb.Error as exc:  # pragma: no cover - environment dependent
        logger.warning("[web-bundle] cannot open %s: %s", path, exc)
        return None


def _connect_dsn(dsn: str) -> duckdb.DuckDBPyConnection | None:
    """Open a warehouse by connection string (local path or ``md:`` MotherDuck)."""
    try:
        return duckdb.connect(dsn)
    except duckdb.Error as exc:  # pragma: no cover - environment dependent
        logger.warning("[web-bundle] cannot open %s: %s", dsn.split("?")[0], exc)
        return None


def _live_positions(store: Any) -> list[dict[str, Any]]:
    rows = store.section("positions")
    return [
        {
            "icao24": r.get("icao24"),
            "callsign": r.get("callsign"),
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
            "alt": r.get("altitude"),
            "velocity": r.get("velocity"),
            "heading": r.get("heading"),
            "vertical_rate": r.get("vertical_rate"),
            "source": r.get("source"),
            "updated_at": r.get("collected_at"),
        }
        for r in rows
        if r.get("latitude") is not None and r.get("longitude") is not None
    ]


def _live_metars(store: Any) -> list[dict[str, Any]]:
    return [
        {
            "icao": r.get("station_icao"),
            "raw_text": r.get("raw_metar"),
            "temperature": r.get("temperature_c"),
            "wind_speed": r.get("wind_speed_kt"),
            "wind_direction": r.get("wind_dir_deg"),
            "visibility": r.get("visibility_m"),
            "cloud_cover": r.get("flight_category"),
            "fetched_at": r.get("timestamp") or r.get("collected_at"),
        }
        for r in store.section("metar")
    ]


def _live_weather(store: Any, airports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the live Open-Meteo forecast store into the bundle weather shape."""
    from datetime import datetime as _dt

    meta = {a["icao"]: a for a in airports}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in store.section("forecast"):
        icao = row.get("station_icao")
        if icao:
            grouped.setdefault(str(icao), []).append(row)

    def _key(row: dict[str, Any]) -> str:
        return str(row.get("timestamp") or "")

    stations: list[dict[str, Any]] = []
    for icao, rows in grouped.items():
        rows.sort(key=_key)
        current_rows = [r for r in rows if not r.get("is_forecast")]
        current = (current_rows or rows)[-1]
        airport = meta.get(icao, {})
        stations.append(
            {
                "icao": icao,
                "name": airport.get("name") or icao,
                "country": airport.get("country") or "",
                "lat": airport.get("lat"),
                "lon": airport.get("lon"),
                "temperature_c": current.get("temperature_c"),
                "wind_speed_ms": current.get("wind_speed_ms"),
                "wind_direction_deg": current.get("wind_direction_deg"),
                "precipitation_mm": current.get("precipitation_mm"),
                "humidity_pct": current.get("humidity_pct"),
                "condition": current.get("condition"),
                "weather_code": current.get("weather_code"),
                "time": str(current.get("timestamp")),
                "hourly": [
                    {
                        "time": str(r.get("timestamp")),
                        "temperature_c": r.get("temperature_c"),
                        "wind_speed_ms": r.get("wind_speed_ms"),
                        "wind_direction_deg": r.get("wind_direction_deg"),
                        "precipitation_mm": r.get("precipitation_mm"),
                        "condition": r.get("condition"),
                        "weather_code": r.get("weather_code"),
                    }
                    for r in rows
                    if r.get("is_forecast")
                ],
            }
        )
    _ = _dt
    stations = [s for s in stations if s.get("lat") is not None and s.get("lon") is not None]
    stations.sort(key=lambda s: str(s.get("icao")))
    return stations


def build_payloads(
    app_settings: Settings | None = None,
    warehouse_connection: str | None = None,
    store: Any = None,
) -> dict[str, Any]:
    """Assemble every dashboard payload from live state + the warehouse.

    This is the single source of truth for both the offline bundle writer and
    the runtime API. ``store`` (a :class:`services.live_store.LiveStore`) supplies
    positions/weather/metar when running inside the collector; otherwise the
    realtime DuckDB store is used, and Silver Parquet supplies the forecast.
    """
    s = app_settings or settings
    warehouse = (
        _connect_dsn(warehouse_connection) if warehouse_connection else _connect(s.duckdb_path)
    )
    realtime = None if store is not None else _connect(s.realtime_db_path)

    written: dict[str, int] = {}
    counts: dict[str, int] = {}

    airports = _load_airports(warehouse)
    counts["airports"] = len(airports)

    positions: list[dict[str, Any]] = []
    metars: list[dict[str, Any]] = []
    notams: list[dict[str, Any]] = []
    stream_health: list[dict[str, Any]] = []
    weather: list[dict[str, Any]]

    if store is not None:
        positions = _live_positions(store)
        metars = _live_metars(store)
        weather = _live_weather(store, airports)
        health = store.health(getattr(s, "live_snapshot_max_age_seconds", 120.0))
        stream_health = [
            {
                "source": name,
                "is_healthy": True,
                **{k: v for k, v in health.items() if k != "sections"},
            }
            for name in health.get("sections", {})
        ]
    else:
        if realtime is not None:
            positions = _rows(
                realtime,
                "SELECT icao24, callsign, ROUND(latitude, 4) AS lat, ROUND(longitude, 4) AS lon, "
                "ROUND(altitude, 0) AS alt, ROUND(velocity, 1) AS velocity, "
                "ROUND(heading, 1) AS heading, ROUND(vertical_rate, 1) AS vertical_rate, "
                "source, CAST(updated_at AS VARCHAR) AS updated_at "
                "FROM live_positions WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 8000",
            )
            metars = _rows(realtime, "SELECT * FROM latest_metar ORDER BY icao")
            notams = _rows(realtime, "SELECT * FROM active_notams")
            stream_health = _rows(realtime, "SELECT * FROM stream_health ORDER BY source")
        weather = _build_weather(s, airports)

    counts["positions"] = len(positions)
    counts["metars"] = len(metars)
    counts["notams"] = len(notams)
    counts["weather_stations"] = len(weather)

    kpis: dict[str, Any] = {
        "total_flights": 0,
        "avg_delay_minutes": 0,
        "cancellation_rate": 0,
        "airports": counts["airports"],
        "airlines": 0,
        "live_aircraft": counts["positions"],
        "metars": counts["metars"],
    }
    analytics: dict[str, Any] = {}
    if warehouse is not None:
        kpis.update(
            {
                "total_flights": _scalar(warehouse, "SELECT COUNT(*) FROM fact_flights", 0),
                "avg_delay_minutes": round(
                    _scalar(
                        warehouse,
                        "SELECT AVG(delay_minutes) FROM fact_flights WHERE status != 'cancelled'",
                        0,
                    )
                    or 0,
                    2,
                ),
                "cancellation_rate": round(
                    (
                        _scalar(
                            warehouse,
                            "SELECT COUNT(*) FROM fact_flights WHERE status = 'cancelled'",
                            0,
                        )
                        / max(_scalar(warehouse, "SELECT COUNT(*) FROM fact_flights", 1), 1)
                    ),
                    4,
                ),
                "airlines": _scalar(warehouse, "SELECT COUNT(*) FROM dim_airline", 0),
            }
        )
        for name in (
            "gold_airport_metrics",
            "gold_airline_rankings",
            "gold_delay_analysis",
            "gold_weather_impact",
            "gold_seasonal_trends",
            "gold_fuel_price_series",
            "gold_aircraft_class_mix",
        ):
            try:
                analytics[name] = _rows(warehouse, f"SELECT * FROM {name}")
            except duckdb.Error:
                analytics[name] = []

        analytics["routes"] = _rows(
            warehouse,
            "SELECT r.origin, r.destination, r.airline, r.stops, r.equipment, r.distance_km, "
            "COUNT(f.flight_id) AS total_flights, AVG(f.delay_minutes) AS avg_delay_minutes "
            "FROM dim_route r LEFT JOIN fact_flights f "
            "ON f.departure_icao = r.origin AND f.arrival_icao = r.destination "
            "GROUP BY 1,2,3,4,5,6 ORDER BY total_flights DESC, distance_km DESC",
        )
        analytics["fleet"] = _rows(
            warehouse,
            "SELECT type_icao, manufacturer, family, engine, capacity, range_km "
            "FROM dim_aircraft ORDER BY capacity DESC",
        )
        analytics["emissions"] = _rows(
            warehouse,
            "SELECT aircraft_type, fuel_burn_liters_per_hour, co2_kg_per_hour "
            "FROM fact_emissions ORDER BY co2_kg_per_hour DESC",
        )
        analytics["notam_summary"] = _rows(
            warehouse,
            "SELECT icao_location, COUNT(*) AS notam_count FROM fact_notams "
            "GROUP BY icao_location ORDER BY notam_count DESC LIMIT 50",
        )
        analytics["status_mix"] = _rows(
            warehouse,
            "SELECT status, COUNT(*) AS flight_count, ROUND(AVG(delay_minutes), 1) AS avg_delay "
            "FROM fact_flights GROUP BY status ORDER BY flight_count DESC",
        )
        analytics["catalog"] = _build_catalog(warehouse)

    ops: dict[str, Any] = {"stream_health": stream_health}
    for name, filename in (
        ("pipeline", "pipeline_report.json"),
        ("quality", "quality_report.json"),
    ):
        path = s.checkpoint_dir / filename
        if path.exists():
            try:
                ops[name] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                ops[name] = None

    live_alts = [p["alt"] for p in positions if p.get("alt")]
    live_avg_alt = sum(live_alts) / len(live_alts) if live_alts else 0.0
    stories = _build_stories(warehouse, counts["positions"], live_avg_alt)

    if weather:
        windiest = max(weather, key=lambda w: float(w.get("wind_speed_ms") or 0))
        warmest = max(
            weather,
            key=lambda w: (
                float(w["temperature_c"]) if w.get("temperature_c") is not None else -99.0
            ),
        )
        stories.append(
            {
                "id": "live-weather",
                "category": "Weather",
                "tone": "warning" if float(windiest.get("wind_speed_ms") or 0) > 12 else "info",
                "title": f"Strongest winds at {windiest['icao']} ({windiest['name']})",
                "metric": f"{float(windiest.get('wind_speed_ms') or 0):.0f}",
                "unit": "m/s sustained",
                "narrative": (
                    f"Live observations across {len(weather)} airports show the strongest "
                    f"winds at {windiest['icao']} ({windiest.get('condition')}), while the warmest "
                    f"station is {warmest['icao']} at "
                    f"{float(warmest.get('temperature_c') or 0):.0f}°C."
                ),
                "chart": {"type": "stat", "data": [{"label": "Stations", "value": len(weather)}]},
            }
        )

    manifest = {
        "version": BUNDLE_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "counts": counts,
        "files": sorted(written),
        "sources": {
            "batch_warehouse": warehouse is not None,
            "live_store": store is not None,
            "weather_stations": len(weather),
            "gold_marts": {k: len(v) for k, v in analytics.items() if k.startswith("gold_")},
        },
    }

    if warehouse is not None:
        warehouse.close()
    if realtime is not None:
        realtime.close()

    return {
        "airports.json": airports,
        "positions.json": positions,
        "metars.json": metars,
        "weather.json": weather,
        "notams.json": notams,
        "kpis.json": kpis,
        "analytics.json": analytics,
        "stories.json": stories,
        "ops.json": ops,
        "manifest.json": manifest,
    }


def build_bundle(app_settings: Settings | None = None) -> dict[str, Any]:
    """Write the offline static bundle to ``web/public/data`` and return its manifest."""
    s = app_settings or settings
    out_dir = PROJECT_ROOT / "web" / "public" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    payloads = build_payloads(s)
    for name, payload in payloads.items():
        _write(out_dir / name, payload)
    logger.info("[web-bundle] wrote %d files → %s", len(payloads), out_dir)
    manifest = payloads["manifest.json"]
    manifest["files"] = sorted(payloads)
    return manifest


# ── airports ────────────────────────────────────────────────────────────────
def _load_airports(con: duckdb.DuckDBPyConnection | None) -> list[dict[str, Any]]:
    reference = json.loads(AIRPORT_REFERENCE.read_text(encoding="utf-8"))
    metrics: dict[str, dict[str, Any]] = {}
    if con is not None:
        try:
            for row in _rows(
                con,
                "SELECT airport_icao, total_flights, avg_delay_minutes, on_time_rate "
                "FROM gold_airport_metrics",
            ):
                metrics[row["airport_icao"]] = row
        except duckdb.Error:
            pass

    airports: list[dict[str, Any]] = []
    for a in reference:
        m = metrics.get(a.get("icao"), {})
        airports.append(
            {
                "icao": a.get("icao"),
                "iata": a.get("iata"),
                "name": a.get("name"),
                "city": a.get("city"),
                "country": a.get("country"),
                "lat": round(a["latitude"], 4),
                "lon": round(a["longitude"], 4),
                "elevation_ft": a.get("altitude_ft"),
                "type": a.get("airport_type", "airport"),
                "total_flights": m.get("total_flights", 0) or 0,
                "avg_delay_minutes": m.get("avg_delay_minutes"),
                "on_time_rate": m.get("on_time_rate"),
            }
        )
    return airports


# ── dbt lineage ─────────────────────────────────────────────────────────────
def _parse_dbt_lineage() -> dict[str, Any]:
    model_dir = PROJECT_ROOT / "dbt" / "models"
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    descriptions: dict[str, str] = {}

    # descriptions from schema.yml files (best effort)
    if yaml is not None:
        for yml in model_dir.rglob("*.yml"):
            try:
                doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001
                logger.debug("[web-bundle] could not parse %s: %s", yml, exc)
                continue
            for model in doc.get("models", []) or []:
                if isinstance(model, dict) and model.get("name"):
                    descriptions[model["name"]] = model.get("description", "") or ""

    for path in sorted(model_dir.rglob("*.sql")):
        rel = path.relative_to(model_dir)
        layer = rel.parts[0] if len(rel.parts) > 1 else "models"
        name = path.stem
        sql = path.read_text(encoding="utf-8")

        materialized = "view"
        config = re.search(r"materialized\s*=\s*'([^']+)'", sql) or re.search(
            r'materialized\s*=\s*"([^"]+)"', sql
        )
        if config:
            materialized = config.group(1)

        deps = set(re.findall(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)", sql))
        deps |= {
            f"source.{s}.{t}"
            for s, t in re.findall(
                r"source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", sql
            )
        }

        nodes.append(
            {
                "id": name,
                "layer": layer,
                "materialized": materialized,
                "description": descriptions.get(name, ""),
                "path": rel.as_posix(),
                "depends_on": sorted(deps),
            }
        )
        for dep in deps:
            edges.append({"from": dep, "to": name})

    # source nodes from sources.yml
    sources: list[dict[str, Any]] = []
    sources_file = model_dir / "sources.yml"
    if sources_file.exists() and yaml is not None:
        try:
            doc = yaml.safe_load(sources_file.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("[web-bundle] could not parse %s: %s", sources_file, exc)
            doc = {}
        for src in doc.get("sources", []) or []:
            for table in src.get("tables", []) or []:
                sources.append(
                    {
                        "id": f"source.{src.get('name')}.{table.get('name')}",
                        "name": table.get("name"),
                        "source": src.get("name"),
                        "description": table.get("description", "") or "",
                    }
                )

    return {"nodes": nodes, "edges": edges, "sources": sources}


# ── catalog (table/column introspection) ────────────────────────────────────
def _build_catalog(con: duckdb.DuckDBPyConnection | None) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    if con is not None:
        for schema, name, kind in con.execute(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_schema, table_name"
        ).fetchall():
            try:
                row_count = _scalar(con, f'SELECT COUNT(*) FROM "{schema}"."{name}"', 0)
            except duckdb.Error:
                row_count = 0
            columns = []
            for col in con.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
                (schema, name),
            ).fetchall():
                columns.append({"name": col[0], "type": col[1], "nullable": col[2] == "YES"})
            layer = (
                "gold"
                if name.startswith("gold_")
                else (
                    "fact"
                    if name.startswith("fact_")
                    else ("dim" if name.startswith("dim_") else "raw")
                )
            )
            tables.append(
                {
                    "schema": schema,
                    "name": name,
                    "kind": kind,
                    "layer": layer,
                    "rows": row_count,
                    "columns": columns,
                }
            )
    return {"tables": tables, "lineage": _parse_dbt_lineage()}


# ── weather (Open-Meteo) ────────────────────────────────────────────────────
def _build_weather(app_settings: Settings, airports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-airport current conditions + 24h hourly forecast (Open-Meteo)."""
    path = app_settings.silver_dir / "weather_forecast" / "data.parquet"
    if not path.exists():
        return []
    try:
        import polars as pl

        df = pl.read_parquet(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[web-bundle] could not read weather_forecast: %s", exc)
        return []

    if df.is_empty() or "station_icao" not in df.columns:
        return []

    meta = {a["icao"]: a for a in airports}
    stations: list[dict[str, Any]] = []

    for icao, group in df.group_by("station_icao", maintain_order=True):
        station_icao = icao[0] if isinstance(icao, tuple) else icao
        group = group.sort("timestamp")
        current_rows = group.filter(pl.col("is_forecast") == False)  # noqa: E712
        current_list = current_rows.tail(1).to_dicts()
        if not current_list:
            current_list = group.tail(1).to_dicts()
        current: dict[str, Any] = current_list[0] if current_list else {}

        hourly = [
            {
                "time": str(r.get("timestamp")),
                "temperature_c": r.get("temperature_c"),
                "wind_speed_ms": r.get("wind_speed_ms"),
                "wind_direction_deg": r.get("wind_direction_deg"),
                "precipitation_mm": r.get("precipitation_mm"),
                "condition": r.get("condition"),
                "weather_code": r.get("weather_code"),
            }
            for r in group.filter(pl.col("is_forecast") == True).to_dicts()  # noqa: E712
        ]

        airport = meta.get(station_icao, {})
        stations.append(
            {
                "icao": station_icao,
                "name": airport.get("name") or station_icao,
                "country": airport.get("country") or "",
                "lat": airport.get("lat"),
                "lon": airport.get("lon"),
                "temperature_c": current.get("temperature_c"),
                "wind_speed_ms": current.get("wind_speed_ms"),
                "wind_direction_deg": current.get("wind_direction_deg"),
                "precipitation_mm": current.get("precipitation_mm"),
                "humidity_pct": current.get("humidity_pct"),
                "condition": current.get("condition"),
                "weather_code": current.get("weather_code"),
                "time": str(current.get("timestamp")),
                "hourly": hourly,
            }
        )

    stations = [s for s in stations if s.get("lat") is not None and s.get("lon") is not None]
    stations.sort(key=lambda s: str(s.get("icao")))
    return stations


# ── stories / insights ──────────────────────────────────────────────────────
def _build_stories(
    con: duckdb.DuckDBPyConnection | None, live_count: int = 0, live_avg_alt: float = 0.0
) -> list[dict[str, Any]]:
    if con is None:
        return []
    stories: list[dict[str, Any]] = []

    total_flights = _scalar(con, "SELECT COUNT(*) FROM fact_flights", 0) or 0
    delayed = (
        _scalar(
            con,
            "SELECT COUNT(*) FROM fact_flights WHERE delay_minutes > 15 AND status != 'cancelled'",
            0,
        )
        or 0
    )
    cancelled = _scalar(con, "SELECT COUNT(*) FROM fact_flights WHERE status = 'cancelled'", 0) or 0
    avg_delay = (
        _scalar(con, "SELECT AVG(delay_minutes) FROM fact_flights WHERE status != 'cancelled'", 0)
        or 0
    )

    # 1. Busiest hub
    top_airports = _rows(
        con,
        "SELECT airport_icao, total_flights, avg_delay_minutes, on_time_rate "
        "FROM gold_airport_metrics ORDER BY total_flights DESC LIMIT 10",
    )
    if top_airports:
        top = top_airports[0]
        share = round(top["total_flights"] / total_flights * 100, 1) if total_flights else 0
        stories.append(
            {
                "id": "busiest-hub",
                "category": "Traffic",
                "tone": "info",
                "title": f"{top['airport_icao']} is the busiest hub in the network",
                "metric": f"{top['total_flights']}",
                "unit": "departures",
                "narrative": (
                    f"{top['airport_icao']} handles {share}% of all observed departures, "
                    f"averaging {top['avg_delay_minutes']:.0f} min delay and a "
                    f"{_pct(top['on_time_rate'])}% on-time rate."
                ),
                "chart": {
                    "type": "bar",
                    "x": "airport_icao",
                    "y": "total_flights",
                    "data": top_airports,
                },
            }
        )

    # 2. Punctuality league
    airlines = _rows(
        con,
        "SELECT airline_icao, airline_name, total_flights, avg_delay_minutes, on_time_rate, rank "
        "FROM gold_airline_rankings ORDER BY on_time_rate DESC",
    )
    if len(airlines) >= 2:
        best, worst = airlines[0], airlines[-1]
        stories.append(
            {
                "id": "punctuality-league",
                "category": "Delays",
                "tone": "warning",
                "title": f"{best['airline_name']} leads on punctuality, {worst['airline_name']} trails",
                "metric": f"{_pct(best['on_time_rate'])}%",
                "unit": "best on-time",
                "narrative": (
                    f"{best['airline_name']} posts a {_pct(best['on_time_rate'])}% on-time rate versus "
                    f"{_pct(worst['on_time_rate'])}% for {worst['airline_name']} — a gap of "
                    f"{round((best['on_time_rate'] - worst['on_time_rate']) * 100, 1)} points."
                ),
                "chart": {
                    "type": "bar",
                    "x": "airline_name",
                    "y": "on_time_rate",
                    "data": airlines,
                },
            }
        )

    # 3. Delay burden
    if total_flights:
        stories.append(
            {
                "id": "delay-burden",
                "category": "Delays",
                "tone": "critical" if delayed / total_flights > 0.3 else "warning",
                "title": "Delay burden across the network",
                "metric": f"{_pct(delayed / total_flights)}%",
                "unit": "flights > 15 min late",
                "narrative": (
                    f"{delayed} of {total_flights} flights arrived more than 15 minutes late; "
                    f"{cancelled} were cancelled. Average delay across active flights is "
                    f"{avg_delay:.0f} minutes."
                ),
                "chart": {
                    "type": "doughnut",
                    "data": [
                        {"label": "On time", "value": total_flights - delayed - cancelled},
                        {"label": "Delayed > 15m", "value": delayed},
                        {"label": "Cancelled", "value": cancelled},
                    ],
                },
            }
        )

    # 4. Weather penalty
    weather = _rows(
        con,
        "SELECT weather_condition, flight_count, avg_delay_minutes, avg_temperature_c "
        "FROM gold_weather_impact WHERE flight_count > 0 ORDER BY avg_delay_minutes DESC",
    )
    if len(weather) >= 2:
        worst_w, best_w = weather[0], weather[-1]
        penalty = round(worst_w["avg_delay_minutes"] - best_w["avg_delay_minutes"], 1)
        stories.append(
            {
                "id": "weather-penalty",
                "category": "Weather",
                "tone": "warning",
                "title": f"{worst_w['weather_condition']} weather adds {penalty:.0f} min of delay",
                "metric": f"+{penalty:.0f}",
                "unit": "minutes vs best",
                "narrative": (
                    f"Flights in {worst_w['weather_condition']} conditions average "
                    f"{worst_w['avg_delay_minutes']:.0f} min delay, compared with "
                    f"{best_w['avg_delay_minutes']:.0f} min under {best_w['weather_condition']}."
                ),
                "chart": {
                    "type": "bar",
                    "x": "weather_condition",
                    "y": "avg_delay_minutes",
                    "data": weather,
                },
            }
        )

    # 5. Peak hour
    hourly = _rows(
        con,
        "SELECT hour_of_day, SUM(flight_count) AS flight_count, AVG(avg_delay_minutes) AS avg_delay "
        "FROM gold_seasonal_trends GROUP BY hour_of_day ORDER BY hour_of_day",
    )
    if hourly:
        peak = max(hourly, key=lambda r: r["flight_count"] or 0)
        stories.append(
            {
                "id": "peak-hour",
                "category": "Traffic",
                "tone": "info",
                "title": f"Aircraft rush hour peaks at {int(peak['hour_of_day']):02d}:00 UTC",
                "metric": f"{int(peak['flight_count'])}",
                "unit": "flights in peak hour",
                "narrative": (
                    f"Network activity concentrates around {int(peak['hour_of_day']):02d}:00 UTC, "
                    f"when delay averages {peak['avg_delay']:.0f} minutes."
                ),
                "chart": {"type": "line", "x": "hour_of_day", "y": "flight_count", "data": hourly},
            }
        )

    # 6. Network reach
    routes = _rows(
        con,
        "SELECT r.origin, r.destination, r.distance_km, COUNT(f.flight_id) AS total_flights, "
        "AVG(f.delay_minutes) AS avg_delay_minutes "
        "FROM dim_route r LEFT JOIN fact_flights f "
        "ON f.departure_icao = r.origin AND f.arrival_icao = r.destination "
        "GROUP BY 1,2,3 ORDER BY total_flights DESC, distance_km DESC",
    )
    if routes:
        longest = max(routes, key=lambda r: r["distance_km"] or 0)
        stories.append(
            {
                "id": "network-reach",
                "category": "Network",
                "tone": "info",
                "title": f"{longest['origin']} → {longest['destination']} is the longest link",
                "metric": f"{longest['distance_km']:.0f}",
                "unit": "km great-circle",
                "narrative": (
                    f"The network spans {len(routes)} routes. The longest is "
                    f"{longest['origin']} to {longest['destination']} at "
                    f"{longest['distance_km']:.0f} km."
                ),
                "chart": {
                    "type": "scatter",
                    "x": "distance_km",
                    "y": "avg_delay_minutes",
                    "data": routes[:80],
                },
            }
        )

    # 7. Emissions
    emissions = _rows(
        con,
        "SELECT aircraft_type, co2_kg_per_hour FROM fact_emissions ORDER BY co2_kg_per_hour DESC LIMIT 10",
    )
    if emissions:
        top_e = emissions[0]
        stories.append(
            {
                "id": "emissions",
                "category": "Emissions",
                "tone": "warning",
                "title": f"{top_e['aircraft_type']} is the highest-emitting type",
                "metric": f"{top_e['co2_kg_per_hour'] / 1000:.1f}",
                "unit": "t CO₂ / hour",
                "narrative": (
                    f"Per-hour CO₂ emissions range from "
                    f"{emissions[-1]['co2_kg_per_hour']:.0f} to "
                    f"{top_e['co2_kg_per_hour']:.0f} kg across monitored aircraft types."
                ),
                "chart": {
                    "type": "bar",
                    "x": "aircraft_type",
                    "y": "co2_kg_per_hour",
                    "data": emissions,
                },
            }
        )

    # 8. Live airspace
    live = live_count
    if live:
        alt = live_avg_alt
        stories.append(
            {
                "id": "live-airspace",
                "category": "Traffic",
                "tone": "info",
                "title": f"{live:,} aircraft currently broadcasting in European airspace",
                "metric": f"{live:,}",
                "unit": "live aircraft",
                "narrative": f"Average cruise altitude of tracked aircraft is {alt:,.0f} ft.",
                "chart": {"type": "stat", "data": [{"label": "Live aircraft", "value": live}]},
            }
        )

    return stories


def main() -> int:
    manifest = build_bundle()
    print(json.dumps(manifest, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
