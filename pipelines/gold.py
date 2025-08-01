"""Gold layer — business-ready analytical marts computed from Silver data.

Gold marts are pre-aggregated, dashboard-ready datasets:
- ``airport_metrics``   : per-airport KPI summary (flights, OTP, avg delay).
- ``airline_rankings``  : per-airline performance ranking.
- ``delay_analysis``    : delay buckets + average/min/max delay.
- ``weather_impact``    : correlation of weather conditions with delays.
- ``seasonal_trends``   : daily/hourly traffic + holiday influence.
- ``fuel_price_series`` : daily fuel price per region.

Outputs are written as compressed Parquet under ``warehouse/gold/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from config.logging import logger
from config.settings import Settings, settings

MART_TABLES = (
    "airport_metrics",
    "airline_rankings",
    "delay_analysis",
    "weather_impact",
    "seasonal_trends",
    "fuel_price_series",
)


def _silver(source: str, app_settings: Settings | None = None) -> pl.DataFrame:
    s = app_settings or settings
    path = s.silver_dir / source / "data.parquet"
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path)


def _gold_path(name: str, app_settings: Settings | None = None) -> Path:
    s = app_settings or settings
    out_dir = s.gold_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{name}.parquet"


def _write_mart(name: str, df: pl.DataFrame, app_settings: Settings | None = None) -> int:
    if df.is_empty():
        logger.info("[gold] mart=%s empty, skipping", name)
        return 0
    path = _gold_path(name, app_settings)
    df.write_parquet(path, compression="zstd")
    logger.info("[gold] mart=%s rows=%s → %s", name, df.height, path)
    return df.height


def airport_metrics(flights: pl.DataFrame) -> pl.DataFrame:
    if flights.is_empty():
        return pl.DataFrame()
    return (
        flights.filter(pl.col("status") != "cancelled")
        .group_by("departure_icao")
        .agg(
            pl.len().alias("total_flights"),
            pl.col("delay_minutes").mean().round(2).alias("avg_delay_minutes"),
            pl.col("delay_minutes").max().alias("max_delay_minutes"),
            (pl.col("delay_minutes") <= settings.delay_threshold_minutes)
            .mean()
            .round(4)
            .alias("on_time_rate"),
        )
        .sort("total_flights", descending=True)
        .rename({"departure_icao": "airport_icao"})
    )


def airline_rankings(flights: pl.DataFrame) -> pl.DataFrame:
    if flights.is_empty():
        return pl.DataFrame()
    return (
        flights.filter(pl.col("status") != "cancelled")
        .group_by("airline_icao")
        .agg(
            pl.col("airline_name").first().alias("airline_name"),
            pl.len().alias("total_flights"),
            pl.col("delay_minutes").mean().round(2).alias("avg_delay_minutes"),
            (pl.col("delay_minutes") <= settings.delay_threshold_minutes)
            .mean()
            .round(4)
            .alias("on_time_rate"),
        )
        .sort("avg_delay_minutes")
        .with_columns(
            pl.col("avg_delay_minutes").rank("ordinal").alias("rank"),
        )
    )


def delay_analysis(flights: pl.DataFrame) -> pl.DataFrame:
    if flights.is_empty():
        return pl.DataFrame()
    return (
        flights.group_by("status")
        .agg(
            pl.len().alias("flight_count"),
            pl.col("delay_minutes").mean().round(2).alias("avg_delay_minutes"),
            pl.col("delay_minutes").min().alias("min_delay_minutes"),
            pl.col("delay_minutes").max().alias("max_delay_minutes"),
        )
        .sort("flight_count", descending=True)
    )


def weather_impact(flights: pl.DataFrame, weather: pl.DataFrame) -> pl.DataFrame:
    if flights.is_empty() or weather.is_empty():
        return pl.DataFrame()
    from pipelines.silver import _as_utc  # local import avoids cycles

    w = (
        weather.rename({"station_icao": "arrival_icao"})
        .with_columns(_as_utc("timestamp", weather.schema.get("timestamp")).alias("timestamp"))
        .select(
            "arrival_icao",
            "timestamp",
            "condition",
            "temperature_c",
            "wind_speed_ms",
            "visibility_m",
        )
    )
    f = (
        flights.filter(pl.col("status") != "cancelled")
        .with_columns(
            pl.coalesce(
                _as_utc("actual_arrival", flights.schema.get("actual_arrival")),
                _as_utc("scheduled_arrival", flights.schema.get("scheduled_arrival")),
            ).alias("arrival_time")
        )
        .select("flight_id", "arrival_icao", "arrival_time", "delay_minutes")
    )
    joined = f.join_asof(
        w.sort("timestamp"),
        left_on="arrival_time",
        right_on="timestamp",
        by="arrival_icao",
        strategy="backward",
    )
    return (
        joined.group_by("condition")
        .agg(
            pl.len().alias("flight_count"),
            pl.col("delay_minutes").mean().round(2).alias("avg_delay_minutes"),
            pl.col("temperature_c").mean().round(1).alias("avg_temperature_c"),
            pl.col("wind_speed_ms").mean().round(1).alias("avg_wind_speed_ms"),
        )
        .sort("flight_count", descending=True)
        .rename({"condition": "weather_condition"})
    )


def seasonal_trends(flights: pl.DataFrame) -> pl.DataFrame:
    if flights.is_empty():
        return pl.DataFrame()
    return (
        flights.with_columns(
            pl.col("scheduled_departure").dt.strftime("%Y-%m-%d").alias("flight_date"),
            pl.col("scheduled_departure").dt.hour().alias("hour_of_day"),
        )
        .group_by("flight_date", "hour_of_day")
        .agg(
            pl.len().alias("flight_count"),
            pl.col("delay_minutes").mean().round(2).alias("avg_delay_minutes"),
        )
        .sort("flight_date", "hour_of_day")
    )


def fuel_price_series(fuel: pl.DataFrame) -> pl.DataFrame:
    if fuel.is_empty():
        return pl.DataFrame()
    return fuel.select("date", "region", "price_per_litre", "currency").sort("date")


def build_marts(app_settings: Settings | None = None) -> dict[str, int]:
    s = app_settings or settings
    flights = _silver("flights", s)
    weather = _silver("weather", s)
    fuel = _silver("fuel", s)

    marts: dict[str, pl.DataFrame] = {
        "airport_metrics": airport_metrics(flights),
        "airline_rankings": airline_rankings(flights),
        "delay_analysis": delay_analysis(flights),
        "weather_impact": weather_impact(flights, weather),
        "seasonal_trends": seasonal_trends(flights),
        "fuel_price_series": fuel_price_series(fuel),
    }

    counts: dict[str, int] = {}
    for name, df in marts.items():
        counts[name] = _write_mart(name, df, s)
    return counts


def _all_marts_empty(counts: dict[str, int]) -> bool:
    return all(count == 0 for count in counts.values())


def summary(counts: dict[str, int]) -> dict[str, Any]:
    return {"marts_built": len([c for c in counts.values() if c > 0]), "rows": counts}


if __name__ == "__main__":
    logger.info("Gold results: %s", build_marts())
