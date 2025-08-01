"""Warehouse — materialise the Star Schema into the persistent DuckDB file.

Builds:
- Dimensions : ``dim_airport``, ``dim_airline``, ``dim_date``, ``dim_fuel``
- Facts      : ``fact_flights``
- Gold marts : registered as DuckDB views (fast, zero-copy over Parquet)

The DuckDB file is the analytics surface served to Superset / the query API.
The build is idempotent: dimensions use an upsert (SCD Type 1) and facts are
rebuilt from Silver Parquet each run.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import duckdb
import polars as pl

from config.logging import logger
from config.settings import Settings, settings


class WarehouseBuilder:
    """Builds and validates the DuckDB Star Schema."""

    def __init__(self, app_settings: Settings | None = None) -> None:
        self.settings = app_settings or settings
        self.db_path = self.settings.duckdb_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.db_path))

    # ── Silver readers ────────────────────────────────────────────────────
    def _read_silver(self, source: str) -> pl.DataFrame:
        path = self.settings.silver_dir / source / "data.parquet"
        if path.exists():
            return pl.read_parquet(path)
        return pl.DataFrame()

    def _read_gold(self, mart: str) -> pl.DataFrame:
        path = self.settings.gold_dir / mart / f"{mart}.parquet"
        if path.exists():
            return pl.read_parquet(path)
        return pl.DataFrame()

    # ── Dimensions ────────────────────────────────────────────────────────
    def build_dim_airport(self) -> None:
        airports_parquet = self.settings.silver_dir / "airports" / "airports.parquet"
        df = pl.read_parquet(airports_parquet) if airports_parquet.exists() else pl.DataFrame()
        if df.is_empty():
            logger.info("[warehouse] dim_airport: no data")
            self._create_or_replace_empty("dim_airport", {"airport_icao": "VARCHAR"})
            return
        clean = df.rename({"ident": "airport_icao"})
        clean = clean.select(
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
        ).unique(subset=["airport_icao"])
        self._load_upsert("dim_airport", clean, ["airport_icao"])
        logger.info("[warehouse] dim_airport rows=%s", clean.height)

    def build_dim_airline(self) -> None:
        flights = self._read_silver("flights")
        if flights.is_empty():
            self._create_or_replace_empty("dim_airline", {"airline_icao": "VARCHAR"})
            return
        airlines = (
            flights.select("airline_icao", "airline_name")
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
            self._create_or_replace_empty("dim_fuel", {"date": "DATE", "region": "VARCHAR"})
            return
        self._load_upsert(
            "dim_fuel",
            fuel.select("date", "region", "price_per_litre", "currency"),
            ["date", "region"],
        )
        logger.info("[warehouse] dim_fuel rows=%s", fuel.height)

    # ── Facts ─────────────────────────────────────────────────────────────
    def build_fact_flights(self) -> None:
        flights = self._read_silver("flights")
        if flights.is_empty():
            self._create_or_replace_empty("fact_flights", {"flight_id": "VARCHAR"})
            return
        fact = flights.select(
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
        )
        self._load_replace("fact_flights", fact)
        logger.info("[warehouse] fact_flights rows=%s", fact.height)

    def build_weather(self) -> None:
        weather = self._read_silver("weather")
        if weather.is_empty():
            self._create_or_replace_empty(
                "weather", {"station_icao": "VARCHAR", "timestamp": "TIMESTAMP"}
            )
            return
        self._load_replace("weather", weather)
        logger.info("[warehouse] weather rows=%s", weather.height)

    # ── Gold views ────────────────────────────────────────────────────────
    def register_gold_views(self) -> None:
        for mart in (
            "airport_metrics",
            "airline_rankings",
            "delay_analysis",
            "weather_impact",
            "seasonal_trends",
            "fuel_price_series",
        ):
            path = self.settings.gold_dir / mart / f"{mart}.parquet"
            if not path.exists():
                continue
            # Point the view at the Parquet file so it persists across connections.
            self.con.execute(
                f"CREATE OR REPLACE VIEW gold_{mart} AS "
                f"SELECT * FROM read_parquet('{path.as_posix()}')"
            )
            logger.info("[warehouse] registered gold view gold_%s → %s", mart, path)

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
        self.build_dim_date()
        self.build_dim_airport()
        self.build_dim_airline()
        self.build_dim_fuel()
        self.build_fact_flights()
        self.build_weather()
        self.register_gold_views()

        summary = self.summary()
        logger.info("[warehouse] build complete → %s", summary)
        return summary

    def summary(self) -> dict[str, int]:
        tables = ("dim_date", "dim_airport", "dim_airline", "dim_fuel", "fact_flights", "weather")
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
        with duckdb.connect(str(self.db_path), read_only=True) as con:
            return con.execute(sql).fetch_df().to_dict(orient="records")

    def close(self) -> None:
        self.con.close()


def build(app_settings: Settings | None = None) -> dict[str, int]:
    builder = WarehouseBuilder(app_settings)
    try:
        return builder.build()
    finally:
        builder.close()


if __name__ == "__main__":
    logger.info("Warehouse build: %s", build())
