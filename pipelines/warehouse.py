"""Warehouse — materialise the Star Schema into the persistent DuckDB file.

Builds:
- Dimensions : ``dim_airport``, ``dim_airline``, ``dim_date``, ``dim_fuel``
- Facts      : ``fact_flights``
- Gold marts : dbt-owned, registered as ``gold_*`` views after ``dbt build``

The DuckDB file is the analytics surface served to Superset / the query API.
The build is idempotent: dimensions use an upsert (SCD Type 1) and facts are
rebuilt from Silver Parquet each run.

After the warehouse step, the orchestrator runs ``dbt build`` which creates
the Gold-layer marts.  ``register_dbt_gold_views()`` then exposes those
dbt views under the ``gold_`` prefix for downstream consumers.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from config.logging import logger
from config.settings import Settings, settings

# Column sets for the facts. When a Silver source is empty the warehouse still
# creates the table with the *full* schema so the dbt staging views compile.
_FACT_FLIGHTS_COLUMNS = (
    "flight_id",
    "callsign",
    "airline_icao",
    "departure_icao",
    "arrival_icao",
    "scheduled_departure",
    "scheduled_arrival",
    "actual_departure",
    "actual_arrival",
    "status",
    "delay_minutes",
    "cancelled",
    "source",
    "ingestion_date",
    "collected_at",
)
_EMPTY_FACT_FLIGHTS = {
    "flight_id": "VARCHAR",
    "callsign": "VARCHAR",
    "airline_icao": "VARCHAR",
    "departure_icao": "VARCHAR",
    "arrival_icao": "VARCHAR",
    "scheduled_departure": "TIMESTAMP",
    "scheduled_arrival": "TIMESTAMP",
    "actual_departure": "TIMESTAMP",
    "actual_arrival": "TIMESTAMP",
    "status": "VARCHAR",
    "delay_minutes": "DOUBLE",
    "cancelled": "BOOLEAN",
    "source": "VARCHAR",
    "ingestion_date": "VARCHAR",
    "collected_at": "VARCHAR",
}

_FACT_EMISSIONS_COLUMNS = (
    "aircraft_type",
    "fuel_burn_liters_per_hour",
    "fuel_burn_kg_per_hour",
    "co2_kg_per_hour",
    "co2_tonnes_per_hour",
    "emission_factor_kg_per_kg_fuel",
    "fuel_density_kg_per_liter",
    "source",
    "collected_at",
)
_EMPTY_FACT_EMISSIONS = {
    "aircraft_type": "VARCHAR",
    "fuel_burn_liters_per_hour": "DOUBLE",
    "fuel_burn_kg_per_hour": "DOUBLE",
    "co2_kg_per_hour": "DOUBLE",
    "co2_tonnes_per_hour": "DOUBLE",
    "emission_factor_kg_per_kg_fuel": "DOUBLE",
    "fuel_density_kg_per_liter": "DOUBLE",
    "source": "VARCHAR",
    "collected_at": "VARCHAR",
}

_FACT_NOTAMS_COLUMNS = (
    "notam_id",
    "icao_location",
    "notam_type",
    "message",
    "qualification",
    "valid_from",
    "valid_to",
    "source",
    "collected_at",
)
_EMPTY_FACT_NOTAMS = {
    "notam_id": "VARCHAR",
    "icao_location": "VARCHAR",
    "notam_type": "VARCHAR",
    "message": "VARCHAR",
    "qualification": "VARCHAR",
    "valid_from": "TIMESTAMP",
    "valid_to": "TIMESTAMP",
    "source": "VARCHAR",
    "collected_at": "VARCHAR",
}

_DIM_AIRPORT_COLUMNS = (
    "airport_icao",
    "name",
    "type",
    "latitude_deg",
    "longitude_deg",
    "elevation_ft",
    "iso_country",
    "municipality",
    "iata_code",
    "score",
)
_DIM_AIRPORT_NUMERIC = {"latitude_deg", "longitude_deg", "elevation_ft", "score"}
_EMPTY_DIM_AIRPORT = {
    c: ("DOUBLE" if c in _DIM_AIRPORT_NUMERIC else "VARCHAR") for c in _DIM_AIRPORT_COLUMNS
}

_DIM_AIRLINE_COLUMNS = ("airline_icao", "airline_name")
_EMPTY_DIM_AIRLINE = {"airline_icao": "VARCHAR", "airline_name": "VARCHAR"}

_DIM_FUEL_COLUMNS = ("date", "region", "price_per_litre", "currency")
_EMPTY_DIM_FUEL = {
    "date": "DATE",
    "region": "VARCHAR",
    "price_per_litre": "DOUBLE",
    "currency": "VARCHAR",
}

_DIM_AIRCRAFT_COLUMNS = (
    "type_icao",
    "type_iata",
    "manufacturer",
    "family",
    "engine",
    "capacity",
    "range_km",
)
_DIM_AIRCRAFT_NUMERIC = {"capacity", "range_km"}
_EMPTY_DIM_AIRCRAFT = {
    c: ("DOUBLE" if c in _DIM_AIRCRAFT_NUMERIC else "VARCHAR") for c in _DIM_AIRCRAFT_COLUMNS
}

_DIM_ROUTE_COLUMNS = ("origin", "destination", "airline", "stops", "equipment", "distance_km")
_DIM_ROUTE_NUMERIC = {"stops", "distance_km"}
_EMPTY_DIM_ROUTE = {
    c: ("DOUBLE" if c in _DIM_ROUTE_NUMERIC else "VARCHAR") for c in _DIM_ROUTE_COLUMNS
}

# Bound the live-position serving table: only aircraft seen inside this window,
# and never more than this many rows (uploaded to Turso every run).
LIVE_POSITION_WINDOW_HOURS = 1
LIVE_POSITION_MAX_ROWS = 5_000

# Eurostat official monthly passengers per airport (benchmark layer).
_EUROSTAT_TRAFFIC_COLUMNS = ("period", "airport_icao", "passengers", "source", "fetched_at")
_EMPTY_EUROSTAT_TRAFFIC = {
    "period": "VARCHAR",
    "airport_icao": "VARCHAR",
    "passengers": "BIGINT",
    "source": "VARCHAR",
    "fetched_at": "VARCHAR",
}

_FACT_POSITIONS_COLUMNS = (
    "icao24",
    "callsign",
    "registration",
    "aircraft_type",
    "aircraft_class",
    "emitter_class",
    "is_cargo",
    "is_military",
    "operator_name",
    "operator_country",
    "operator_category",
    "type_name",
    "manufacturer",
    "airframe",
    "wake_category",
    "latitude",
    "longitude",
    "altitude",
    "velocity",
    "heading",
    "vertical_rate",
    "squawk",
    "emergency",
    "co2_kg_per_hour",
    "fuel_burn_kg_per_hour",
    "co2_estimated",
    "source",
    "collected_at",
)
_POSITION_NUMERIC = {
    "latitude",
    "longitude",
    "altitude",
    "velocity",
    "heading",
    "vertical_rate",
    "co2_kg_per_hour",
    "fuel_burn_kg_per_hour",
}
_POSITION_BOOLEAN = {"is_cargo", "is_military", "co2_estimated"}
_EMPTY_FACT_POSITIONS = {
    column: (
        "BOOLEAN"
        if column in _POSITION_BOOLEAN
        else "DOUBLE"
        if column in _POSITION_NUMERIC
        else "VARCHAR"
    )
    for column in _FACT_POSITIONS_COLUMNS
}
_EMPTY_WEATHER = {
    "station_icao": "VARCHAR",
    "timestamp": "TIMESTAMP",
    "temperature_c": "DOUBLE",
    "humidity_pct": "DOUBLE",
    "wind_speed_ms": "DOUBLE",
    "visibility_m": "DOUBLE",
    "condition": "VARCHAR",
    "pressure_hpa": "DOUBLE",
    "source": "VARCHAR",
    "collected_at": "VARCHAR",
    "ingestion_date": "VARCHAR",
    "name": "VARCHAR",
    "latitude": "DOUBLE",
    "longitude": "DOUBLE",
    "dewpoint_c": "DOUBLE",
    "wind_dir_deg": "DOUBLE",
    "wind_speed_kt": "DOUBLE",
    "gust_kt": "DOUBLE",
    "visibility_raw": "VARCHAR",
    "flight_category": "VARCHAR",
    "raw_metar": "VARCHAR",
}

_WEATHER_COLUMNS = tuple(_EMPTY_WEATHER.keys())


def _ensure_columns(
    df: pl.DataFrame,
    columns: tuple[str, ...],
    numeric: set[str] | frozenset[str] = frozenset(),
    boolean: set[str] | frozenset[str] = frozenset(),
) -> pl.DataFrame:
    """Project a frame onto a fixed schema, filling absent columns with NULLs.

    dbt staging views reference the full column set; a partial Silver frame (or
    an empty one) would otherwise leave them missing and fail to compile.
    """
    missing = [c for c in columns if c not in df.columns]
    if missing:
        exprs = []
        for column in missing:
            if column in boolean:
                exprs.append(pl.lit(None, dtype=pl.Boolean).alias(column))
            elif column in numeric:
                exprs.append(pl.lit(None, dtype=pl.Float64).alias(column))
            else:
                exprs.append(pl.lit(None, dtype=pl.Utf8).alias(column))
        df = df.with_columns(exprs)
    return df.select(list(columns))


class WarehouseBuilder:
    """Builds and validates the DuckDB Star Schema."""

    def __init__(self, app_settings: Settings | None = None) -> None:
        self.settings = app_settings or settings
        self.db_path = self.settings.duckdb_path
        # Build into MotherDuck when configured, otherwise the local file.
        self.connection = self.settings.warehouse_connection
        if not self.connection.startswith("md:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(self.connection)
        if self.connection.startswith("md:"):
            logger.info(
                "[warehouse] connected to MotherDuck (%s)", self.settings.motherduck_database
            )

    # ── Silver readers ────────────────────────────────────────────────────
    def _read_silver(self, source: str) -> pl.DataFrame:
        path = self.settings.silver_dir / source / "data.parquet"
        if path.exists():
            return pl.read_parquet(path)
        return pl.DataFrame()

    # ── Dimensions ────────────────────────────────────────────────────────
    def build_dim_airport(self) -> None:
        airports_parquet = self.settings.silver_dir / "airports" / "airports.parquet"
        df = pl.read_parquet(airports_parquet) if airports_parquet.exists() else pl.DataFrame()
        if df.is_empty():
            logger.info("[warehouse] dim_airport: no data")
            self._create_or_replace_empty("dim_airport", _EMPTY_DIM_AIRPORT)
            return
        clean = df.rename({"ident": "airport_icao"})
        clean = _ensure_columns(clean, _DIM_AIRPORT_COLUMNS, _DIM_AIRPORT_NUMERIC).unique(
            subset=["airport_icao"]
        )
        self._load_upsert("dim_airport", clean, ["airport_icao"])
        logger.info("[warehouse] dim_airport rows=%s", clean.height)

    def build_dim_airline(self) -> None:
        flights = self._read_silver("flights")
        if flights.is_empty():
            self._create_or_replace_empty("dim_airline", _EMPTY_DIM_AIRLINE)
            return
        airlines = (
            _ensure_columns(flights, _DIM_AIRLINE_COLUMNS)
            .filter(pl.col("airline_icao").is_not_null())
            .unique(subset=["airline_icao"])
        )
        self._load_upsert("dim_airline", airlines, ["airline_icao"])
        logger.info("[warehouse] dim_airline rows=%s", airlines.height)

    def build_dim_date(self, start: str = "2024-01-01", end: str | None = None) -> None:
        end = end or _dt.datetime.now(_dt.UTC).date().isoformat()
        dates = pl.date_range(
            pl.lit(start).str.to_date(), pl.lit(end).str.to_date(), "1d", eager=True
        ).to_frame("date")
        dim = dates.with_columns(
            pl.col("date").dt.year().alias("year"),
            pl.col("date").dt.month().alias("month"),
            pl.col("date").dt.day().alias("day"),
            pl.col("date").dt.weekday().alias("day_of_week"),
            pl.col("date").dt.quarter().alias("quarter"),
            (pl.col("date").dt.weekday() >= 6).alias("is_weekend"),
            pl.col("date").dt.strftime("%B").alias("month_name"),
        )
        self._load_upsert("dim_date", dim, ["date"])
        logger.info("[warehouse] dim_date rows=%s", dim.height)

    def build_dim_fuel(self) -> None:
        fuel = self._read_silver("fuel")
        if fuel.is_empty():
            self._create_or_replace_empty("dim_fuel", _EMPTY_DIM_FUEL)
            return
        self._load_upsert(
            "dim_fuel",
            _ensure_columns(fuel, _DIM_FUEL_COLUMNS, {"price_per_litre"}),
            ["date", "region"],
        )
        logger.info("[warehouse] dim_fuel rows=%s", fuel.height)

    def build_dim_aircraft(self) -> None:
        aircraft = self._read_silver("aircraft")
        if aircraft.is_empty():
            self._create_or_replace_empty("dim_aircraft", _EMPTY_DIM_AIRCRAFT)
            return
        dim = _ensure_columns(aircraft, _DIM_AIRCRAFT_COLUMNS, _DIM_AIRCRAFT_NUMERIC).unique(
            subset=["type_icao"]
        )
        self._load_upsert("dim_aircraft", dim, ["type_icao"])
        logger.info("[warehouse] dim_aircraft rows=%s", dim.height)

    def build_dim_route(self) -> None:
        routes = self._read_silver("routes")
        if routes.is_empty():
            self._create_or_replace_empty("dim_route", _EMPTY_DIM_ROUTE)
            return
        dim = _ensure_columns(routes, _DIM_ROUTE_COLUMNS, _DIM_ROUTE_NUMERIC).unique(
            subset=["origin", "destination", "airline"]
        )
        self._load_upsert("dim_route", dim, ["origin", "destination", "airline"])
        logger.info("[warehouse] dim_route rows=%s", dim.height)

    def build_fact_emissions(self) -> None:
        emissions = self._read_silver("emissions")
        if emissions.is_empty():
            self._create_or_replace_empty("fact_emissions", _EMPTY_FACT_EMISSIONS)
            return
        fact = _ensure_columns(emissions, _FACT_EMISSIONS_COLUMNS)
        self._load_replace("fact_emissions", fact)
        logger.info("[warehouse] fact_emissions rows=%s", fact.height)

    def build_fact_notams(self) -> None:
        notams = self._read_silver("notams")
        if notams.is_empty():
            self._create_or_replace_empty("fact_notams", _EMPTY_FACT_NOTAMS)
            return
        fact = _ensure_columns(notams, _FACT_NOTAMS_COLUMNS)
        self._load_replace("fact_notams", fact)
        logger.info("[warehouse] fact_notams rows=%s", fact.height)

    # ── Facts ─────────────────────────────────────────────────────────────
    def build_fact_flights(self) -> None:
        flights = self._read_silver("flights")
        if flights.is_empty():
            self._create_or_replace_empty("fact_flights", _EMPTY_FACT_FLIGHTS)
            return
        fact = _ensure_columns(flights, _FACT_FLIGHTS_COLUMNS, {"delay_minutes"}, {"cancelled"})
        if "status" in fact.columns:
            fact = fact.with_columns(
                pl.col("status")
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.to_lowercase()
                .replace({"en_route": "en-route", "enroute": "en-route", "en route": "en-route"})
                .alias("status")
            )
        if "source" in fact.columns and "delay_minutes" in fact.columns:
            # OpenSky movements carry no schedule; a historical 0 is not a
            # measurement and must never reach the serving copy.
            fact = fact.with_columns(
                pl.when(pl.col("source") == "opensky")
                .then(None)
                .otherwise(pl.col("delay_minutes"))
                .alias("delay_minutes")
            )
        if "collected_at" in fact.columns:
            # Live rows have no ingestion_date; the serving watermark and the
            # freshness report both need one.
            fact = fact.with_columns(
                pl.coalesce(
                    pl.col("ingestion_date"),
                    pl.col("collected_at").cast(pl.Utf8).str.slice(0, 10),
                ).alias("ingestion_date")
            )
        self._load_replace("fact_flights", fact)
        logger.info("[warehouse] fact_flights rows=%s", fact.height)

    def build_weather(self) -> None:
        weather = self._read_silver("weather")
        if weather.is_empty():
            self._create_or_replace_empty("weather", _EMPTY_WEATHER)
            return
        weather = _ensure_columns(
            weather,
            _WEATHER_COLUMNS,
            numeric={"temperature_c", "humidity_pct", "wind_speed_ms", "visibility_m"},
        )
        self._load_replace("weather", weather)
        logger.info("[warehouse] weather rows=%s", weather.height)

    def build_eurostat_traffic(self) -> None:
        """Official Eurostat monthly passengers per airport (if fetched)."""
        path = (
            Path(self.settings.project_root)
            / "services"
            / "data"
            / "eurostat_airport_traffic.parquet"
        )
        if not path.exists():
            self._create_or_replace_empty("eurostat_airport_traffic", _EMPTY_EUROSTAT_TRAFFIC)
            return
        frame = _ensure_columns(pl.read_parquet(path), _EUROSTAT_TRAFFIC_COLUMNS)
        self._load_replace("eurostat_airport_traffic", frame)
        logger.info("[warehouse] eurostat_airport_traffic rows=%s", frame.height)

    def build_fact_positions(self) -> None:
        """Latest live aircraft position per airframe (with class + CO₂).

        Bounded twice: only aircraft seen within the recent window of the data
        (the site shows the current picture, not every airframe ever tracked),
        and at most ``LIVE_POSITION_MAX_ROWS`` rows. Without this the table
        grew without bound and every Turso run re-uploaded all of it.
        """
        positions = self._read_silver("positions")
        if positions.is_empty():
            self._create_or_replace_empty("fact_positions", _EMPTY_FACT_POSITIONS)
            return
        fact = _ensure_columns(
            positions, _FACT_POSITIONS_COLUMNS, _POSITION_NUMERIC, _POSITION_BOOLEAN
        )
        if "collected_at" in fact.columns:
            # Silver keeps collected_at as an ISO string; parse just for the
            # window comparison and keep the original column untouched.
            fact = fact.with_columns(
                pl.col("collected_at")
                .str.to_datetime(strict=False, time_zone="UTC")
                .alias("_seen_at")
            )
            cutoff = fact["_seen_at"].max()
            if isinstance(cutoff, _dt.datetime):
                fact = fact.filter(
                    pl.col("_seen_at") >= cutoff - _dt.timedelta(hours=LIVE_POSITION_WINDOW_HOURS)
                )
            # Newest first: dedup keeps the freshest row per airframe, and the
            # cap keeps the most recent aircraft.
            fact = fact.sort("_seen_at", descending=True)
            fact = fact.unique(subset=["icao24"], keep="first")
            fact = fact.head(LIVE_POSITION_MAX_ROWS).drop("_seen_at")
        else:
            fact = fact.unique(subset=["icao24"], keep="last")
        self._load_replace("fact_positions", fact)
        logger.info("[warehouse] fact_positions rows=%s", fact.height)

    # ── Gold views ────────────────────────────────────────────────────────
    def register_dbt_gold_views(self) -> None:
        """Expose every dbt ``marts`` model as a ``gold_*`` view in ``main``.

        Discovered dynamically so all 11 marts surface to the dashboard, not a
        hardcoded subset. When dbt is unavailable, falls back to the Python-built
        Gold Parquet under ``warehouse/gold/<mart>/<mart>.parquet``.
        """
        mart_names = [
            row[0]
            for row in self.con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'marts' ORDER BY table_name"
            ).fetchall()
        ]
        if mart_names:
            for mart in mart_names:
                self.con.execute(
                    f'CREATE OR REPLACE VIEW gold_{mart} AS SELECT * FROM marts."{mart}"'
                )
                logger.info("[warehouse] registered gold_%s ← marts.%s (dbt)", mart, mart)
            return

        # Fallback: Python-built Gold Parquet from pipelines.gold.
        for parquet in sorted(self.settings.gold_dir.glob("*/*.parquet")):
            mart = parquet.stem
            self.con.execute(
                f"CREATE OR REPLACE VIEW gold_{mart} AS "
                f"SELECT * FROM read_parquet('{parquet.as_posix()}')"
            )
            logger.info("[warehouse] registered gold_%s ← %s (parquet)", mart, parquet.name)

    # ── Low-level writers ─────────────────────────────────────────────────
    def _load_upsert(self, table: str, df: pl.DataFrame, keys: list[str]) -> None:
        """SCD Type 1 upsert on ``keys``."""
        self.con.register(f"_staging_{table}", df.to_arrow())
        key_sql = ", ".join(f'"{k}"' for k in keys)
        self.con.execute(
            f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM _staging_{table} WHERE FALSE"
        )
        self.con.execute(
            f"""
            DELETE FROM {table} WHERE ({key_sql}) IN (
                SELECT {key_sql} FROM _staging_{table}
            )
            """
        )
        self.con.execute(f"INSERT INTO {table} SELECT * FROM _staging_{table}")

    def _load_replace(self, table: str, df: pl.DataFrame) -> None:
        self.con.register(f"_staging_{table}", df.to_arrow())
        self.con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _staging_{table}")

    def _create_or_replace_empty(self, table: str, schema: dict[str, str]) -> None:
        cols = ", ".join(f'"{name}" {dtype}' for name, dtype in schema.items())
        self.con.execute(f"CREATE OR REPLACE TABLE {table} ({cols})")

    # ── Public API ────────────────────────────────────────────────────────
    def build(self) -> dict[str, int]:
        # Dimensions
        self.build_dim_date()
        self.build_dim_airport()
        self.build_dim_airline()
        self.build_dim_fuel()
        self.build_dim_aircraft()
        self.build_dim_route()

        # Facts
        self.build_fact_flights()
        self.build_weather()
        self.build_fact_positions()
        self.build_eurostat_traffic()
        self.build_fact_emissions()
        self.build_fact_notams()

        # Gold views are now registered by the orchestrator after dbt build
        # (see register_dbt_gold_views)

        summary = self.summary()
        logger.info("[warehouse] build complete → %s", summary)
        return summary

    def summary(self) -> dict[str, int]:
        tables = (
            "dim_date",
            "dim_airport",
            "dim_airline",
            "dim_fuel",
            "dim_aircraft",
            "dim_route",
            "fact_flights",
            "weather",
            "fact_positions",
            "fact_emissions",
            "fact_notams",
        )
        rows: dict[str, int] = {}
        for table in tables:
            try:
                count_row = self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                count = int(count_row[0]) if count_row is not None else 0
            except duckdb.Error:
                count = 0
            rows[table] = int(count)
        return rows

    def query(self, sql: str) -> list[dict[str, Any]]:
        """Run an arbitrary read-only query and return rows as dicts."""
        with duckdb.connect(self.settings.serving_connection, read_only=True) as con:
            return con.execute(sql).fetch_df().to_dict(orient="records")

    def close(self) -> None:
        self.con.close()


def build(app_settings: Settings | None = None) -> dict[str, int]:
    builder = WarehouseBuilder(app_settings)
    try:
        return builder.build()
    finally:
        builder.close()


def register_gold(app_settings: Settings | None = None) -> None:
    """Register ``gold_*`` views from the dbt ``marts`` schema (post-``dbt build``)."""
    builder = WarehouseBuilder(app_settings)
    try:
        builder.register_dbt_gold_views()
    finally:
        builder.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build the DuckDB star schema / register Gold")
    parser.add_argument("--register-gold", action="store_true", help="register gold_* views")
    args = parser.parse_args()
    if args.register_gold:
        register_gold()
    else:
        logger.info("Warehouse build: %s", build())
