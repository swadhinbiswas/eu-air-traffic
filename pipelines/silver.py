"""Silver layer — clean, validate, and deduplicate Bronze records with Polars.

Each source has a dedicated transform that:
1. Normalises timestamps to UTC.
2. Applies validation rules (bad rows → quarantine "dead letter queue").
3. Deduplicates on natural keys (idempotency).
4. Writes partitioned Parquet under ``warehouse/silver/<source>/``.

Validation failures never abort a run; they land in ``warehouse/quarantine`` so
an analyst can inspect them (per the documented error-handling strategy).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from config.logging import logger
from config.settings import Settings, settings


def UTC_NOW() -> str:
    return datetime.now(UTC).isoformat()


# ── Shared helpers ───────────────────────────────────────────────────────────
def _as_utc(column: str, dtype: pl.DataType | None = None) -> pl.Expr:
    """Normalise a column to UTC-aware datetime, whatever its source dtype.

    Pass ``dtype`` (from ``df.schema[column]``) when the caller already knows
    it, so already-parsed datetime columns are passed through untouched and
    all-null columns are cast instead of string-parsed.
    """
    expr = pl.col(column)

    if isinstance(dtype, pl.Datetime):
        return expr.dt.replace_time_zone("UTC")

    if dtype == pl.Null:
        return expr.cast(pl.Datetime("us")).dt.replace_time_zone("UTC")

    # String (or unknown) → parse offset-aware and naive forms, coalesce.
    aware = expr.str.to_datetime("%Y-%m-%dT%H:%M:%S%.f%z", strict=False)
    aware_utc = (
        aware.dt.convert_time_zone("UTC").dt.replace_time_zone(None).dt.replace_time_zone("UTC")
    )
    naive = expr.str.to_datetime("%Y-%m-%dT%H:%M:%S%.f", strict=False).dt.replace_time_zone("UTC")
    date_only = expr.str.to_datetime("%Y-%m-%d", strict=False).dt.replace_time_zone("UTC")
    return pl.coalesce(aware_utc, naive, date_only).dt.replace_time_zone("UTC")


def _split_quarantine(
    df: pl.DataFrame,
    valid_mask: pl.Expr,
    source: str,
    reason: str,
    app_settings: Settings | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Partition rows into (valid, quarantined) dataframes."""
    mask = df.with_columns(valid_mask.alias("_valid"))["_valid"]
    valid = df.filter(mask)
    bad = df.filter(~mask)
    if not bad.is_empty():
        bad = bad.with_columns(
            pl.lit(reason).alias("quarantine_reason"),
            pl.lit(UTC_NOW()).alias("quarantined_at"),
        )
        _write_quarantine(bad, source, app_settings)
    return valid.drop("_valid") if "_valid" in valid.columns else valid, bad


def _write_quarantine(df: pl.DataFrame, source: str, app_settings: Settings | None = None) -> None:
    s = app_settings or settings
    out_dir = s.quarantine_dir / source
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"quarantine_{UTC_NOW().replace(':', '').replace('-', '')}.parquet"
    df.write_parquet(path)
    logger.warning("[silver] quarantined %s rows for source=%s → %s", df.height, source, path)


def _write_partitioned(
    df: pl.DataFrame,
    source: str,
    dedup_on: list[str] | None = None,
    app_settings: Settings | None = None,
) -> Path:
    s = app_settings or settings
    out_dir = s.silver_dir / source
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "data.parquet"
    if path.exists():
        existing = pl.read_parquet(path)
        df = pl.concat([existing, df], how="diagonal_relaxed")
    present = [c for c in (dedup_on or []) if c in df.columns]
    if present:
        df = df.unique(subset=present, keep="last")
    df.write_parquet(path, compression="zstd")
    return path


# ── Source transforms ────────────────────────────────────────────────────────
def transform_flights(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    if df.is_empty():
        return df
    keep = [
        "flight_id",
        "callsign",
        "airline_icao",
        "airline_name",
        "departure_icao",
        "departure_iata",
        "arrival_icao",
        "arrival_iata",
        "scheduled_departure",
        "scheduled_arrival",
        "actual_departure",
        "actual_arrival",
        "status",
        "delay_minutes",
        "cancelled",
        "source",
        "collected_at",
        "ingestion_date",
    ]
    present = [c for c in keep if c in df.columns]

    df = df.select(present)
    for col in ("scheduled_departure", "scheduled_arrival", "actual_departure", "actual_arrival"):
        if col in df.columns:
            df = df.with_columns(_as_utc(col, df.schema.get(col)).alias(col))

    if "delay_minutes" in df.columns:
        df = df.with_columns(
            pl.col("delay_minutes")
            .cast(pl.Float64)
            .fill_null(0)
            .clip(lower_bound=0)
            .alias("delay_minutes")
        )

    if "cancelled" in df.columns:
        df = df.with_columns(pl.col("cancelled").cast(pl.Boolean).fill_null(False))

    valid = (
        pl.col("flight_id").is_not_null()
        & (pl.col("flight_id") != "")
        & pl.col("departure_icao").is_not_null()
        & pl.col("arrival_icao").is_not_null()
        & (pl.col("departure_icao") != pl.col("arrival_icao"))
    )
    df, _ = _split_quarantine(
        df, valid, "flights", "missing or invalid flight/departure/arrival keys", app_settings
    )

    if df.is_empty():
        return df
    df = df.unique(subset=["flight_id"], keep="last")
    if "scheduled_departure" in df.columns:
        df = df.sort("scheduled_departure", nulls_last=True)
    return df


def transform_airports(df: pl.DataFrame) -> tuple[tuple[str, pl.DataFrame], ...]:
    """Explode nested airport metadata into normalised Silver tables."""
    if df.is_empty():
        return ()

    runways, freqs, navaids, stations = [], [], [], []
    airport_rows: list[dict] = []

    for row in df.to_dicts():
        ident = row.get("icao") or row.get("ident")
        if not ident:
            continue
        airport_rows.append(
            {
                "ident": ident,
                "name": row.get("name"),
                "type": row.get("type"),
                "latitude_deg": row.get("latitude_deg") or row.get("latitude"),
                "longitude_deg": row.get("longitude_deg") or row.get("longitude"),
                "elevation_ft": row.get("elevation_ft"),
                "iso_country": row.get("iso_country") or row.get("country"),
                "municipality": row.get("municipality") or row.get("city"),
                "iata_code": row.get("iata_code") or row.get("iata"),
                "score": row.get("score"),
                "scheduled_service": row.get("scheduled_service"),
                "ingestion_date": row.get("ingestion_date"),
            }
        )
        for rw in row.get("runways") or []:
            rw = dict(rw)
            rw["airport_ident"] = ident
            runways.append(rw)
        for fq in row.get("freqs") or []:
            fq = dict(fq)
            fq["airport_ident"] = ident
            freqs.append(fq)
        for nav in row.get("navaids") or []:
            nav = dict(nav)
            nav["airport_ident"] = ident
            navaids.append(nav)
        station = row.get("station")
        if station:
            stations.append(
                {
                    "airport_ident": ident,
                    **{k: v for k, v in station.items() if k != "airport_ident"},
                }
            )

    results = []
    if airport_rows:
        results.append(("airports", pl.DataFrame(airport_rows)))
    if runways:
        results.append(("runways", pl.DataFrame(runways)))
    if freqs:
        results.append(("frequencies", pl.DataFrame(freqs)))
    if navaids:
        results.append(("navaids", pl.DataFrame(navaids)))
    if stations:
        results.append(("stations", pl.DataFrame(stations)))
    return tuple(results)


def transform_weather(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    if df.is_empty():
        return df
    df = df.with_columns(_as_utc("timestamp", df.schema.get("timestamp")).alias("timestamp"))
    valid = (
        pl.col("station_icao").is_not_null()
        & pl.col("temperature_c").is_not_null()
        & pl.col("temperature_c").is_between(-80.0, 60.0)
    )
    df, _ = _split_quarantine(
        df, valid, "weather", "invalid station or temperature outside [-80, 60]", app_settings
    )
    if df.is_empty():
        return df
    return df.unique(subset=["station_icao", "timestamp"], keep="last")


def transform_holidays(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    if df.is_empty():
        return df
    valid = (
        pl.col("country").is_not_null()
        & pl.col("date").is_not_null()
        & pl.col("name").is_not_null()
    )
    df, _ = _split_quarantine(df, valid, "holidays", "missing country/date/name", app_settings)
    if df.is_empty():
        return df
    return df.unique(subset=["country", "date"], keep="last")


def transform_fuel(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    if df.is_empty():
        return df
    valid = pl.col("date").is_not_null() & pl.col("price_per_litre").is_not_null()
    df, _ = _split_quarantine(df, valid, "fuel", "missing date or price", app_settings)
    if df.is_empty():
        return df
    return df.unique(subset=["date", "region"], keep="last")


_TRANSFORMS: dict[str, Callable[[pl.DataFrame, Settings | None], pl.DataFrame]] = {
    "flights": transform_flights,
    "weather": transform_weather,
    "holidays": transform_holidays,
    "fuel": transform_fuel,
}


def _read_bronze(source: str, app_settings: Settings | None = None) -> pl.DataFrame:
    """Read the consolidated Bronze Parquet for a source (Medallion input)."""
    s = app_settings or settings
    source_dir = s.bronze_dir / "parquet" / source
    if not source_dir.exists():
        return pl.DataFrame()
    files = sorted(source_dir.glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    frames = [pl.read_parquet(path) for path in files]
    frames = [f for f in frames if not f.is_empty()]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def run(source: str, app_settings: Settings | None = None) -> int:
    """Run the silver transform for one source. Returns rows written."""
    s = app_settings or settings
    df = _read_bronze(source, s)
    if df.is_empty():
        logger.info("[silver] no bronze data for source=%s", source)
        return 0
    logger.info("[silver] source=%s raw rows=%s", source, df.height)

    if source == "airports":
        outputs = transform_airports(df)
        if not outputs:
            return 0
        airports_dir = s.silver_dir / "airports"
        airports_dir.mkdir(parents=True, exist_ok=True)
        for table_name, table_df in outputs:
            path = airports_dir / f"{table_name}.parquet"
            if path.exists():
                existing = pl.read_parquet(path)
                table_df = pl.concat([existing, table_df], how="diagonal_relaxed")
            dedup_keys = {
                "airports": ["ident"],
                "runways": ["id"],
                "frequencies": ["id"],
                "navaids": ["id"],
                "stations": ["airport_ident", "icao_code"],
            }.get(table_name)
            present = [c for c in (dedup_keys or []) if c in table_df.columns]
            if present:
                table_df = table_df.unique(subset=present, keep="last")
            table_df.write_parquet(path, compression="zstd")
            logger.info(
                "[silver] source=airports table=%s rows=%s → %s", table_name, table_df.height, path
            )
        return df.height

    transform = _TRANSFORMS.get(source)
    if transform is None:
        logger.warning("[silver] no transform registered for source=%s", source)
        return 0
    clean = transform(df, s)
    if clean.is_empty():
        return 0
    dedup_keys = {
        "flights": ["flight_id"],
        "weather": ["station_icao", "timestamp"],
        "holidays": ["country", "date"],
        "fuel": ["date", "region"],
    }.get(source)
    path = _write_partitioned(clean, source, dedup_on=dedup_keys, app_settings=s)
    logger.info("[silver] source=%s clean rows=%s → %s", source, clean.height, path)
    return clean.height


def run_all(
    sources: list[str] | None = None, app_settings: Settings | None = None
) -> dict[str, int]:
    from ingestion.registry import available

    targets = sources or available()
    results: dict[str, int] = {}
    for source in targets:
        results[source] = run(source, app_settings)
    return results


if __name__ == "__main__":
    logger.info("Silver results: %s", run_all())
