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

import json
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
    # RFC3339 "Z" suffix (e.g. "2026-09-11T13:00:00.000Z") is not matched by
    # %z, so normalise it to an explicit offset first.
    zulu = (
        expr.str.replace("Z", "+00:00", literal=True)
        .str.to_datetime("%Y-%m-%dT%H:%M:%S%.f%z", strict=False)
        .dt.convert_time_zone("UTC")
        .dt.replace_time_zone(None)
        .dt.replace_time_zone("UTC")
    )
    naive = expr.str.to_datetime("%Y-%m-%dT%H:%M:%S%.f", strict=False).dt.replace_time_zone("UTC")
    date_only = expr.str.to_datetime("%Y-%m-%d", strict=False).dt.replace_time_zone("UTC")
    return pl.coalesce(aware_utc, zulu, naive, date_only).dt.replace_time_zone("UTC")


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
        # Align historical rows to the transform's canonical dtypes so schema
        # evolution (e.g. numeric visibility) applies to previously written data
        # instead of being reverted by the string→number concat.
        for name, dtype in df.schema.items():
            if name in existing.columns and existing.schema[name] != dtype:
                existing = existing.with_columns(pl.col(name).cast(dtype, strict=False).alias(name))
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

    # OpenSky usually knows only one end of a movement: a departure query gives
    # the origin, an arrival query the destination. Require at least one known
    # airport instead of both, and only reject when the two ends agree.
    has_departure = pl.col("departure_icao").is_not_null() & (pl.col("departure_icao") != "")
    has_arrival = pl.col("arrival_icao").is_not_null() & (pl.col("arrival_icao") != "")
    ends_differ = (
        pl.col("departure_icao").is_null()
        | pl.col("arrival_icao").is_null()
        | (pl.col("departure_icao") != pl.col("arrival_icao"))
    )
    valid = (
        pl.col("flight_id").is_not_null()
        & (pl.col("flight_id") != "")
        & (has_departure | has_arrival)
        & ends_differ
    )
    df, _ = _split_quarantine(
        df, valid, "flights", "missing or invalid flight/departure/arrival keys", app_settings
    )

    if df.is_empty():
        return df
    # The same flight can be observed twice (once at each end). Dedup keeps the
    # last row, so order the most complete record last and let it win.
    df = df.with_columns(
        (
            pl.col("departure_icao").is_null().cast(pl.Int8)
            + pl.col("arrival_icao").is_null().cast(pl.Int8)
        ).alias("_missing_ends")
    )
    df = df.sort(["_missing_ends", "flight_id"], descending=[True, False]).drop("_missing_ends")
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
        & pl.col("timestamp").is_not_null()
        & pl.col("temperature_c").is_not_null()
        & pl.col("temperature_c").is_between(-80.0, 60.0)
    )
    df, _ = _split_quarantine(
        df,
        valid,
        "weather",
        "invalid station/timestamp or temperature outside [-80, 60]",
        app_settings,
    )
    if df.is_empty():
        return df
    # Silver guarantees types: METAR rows can carry raw visibility strings
    # ("6+"), which would otherwise leave the column as VARCHAR downstream.
    numeric = [
        c
        for c in ("wind_speed_ms", "visibility_m", "humidity_pct", "pressure_hpa")
        if c in df.columns
    ]
    if numeric:
        df = df.with_columns([pl.col(c).cast(pl.Float64, strict=False).alias(c) for c in numeric])
    return df.unique(subset=["station_icao", "timestamp"], keep="last")


def transform_weather_forecast(
    df: pl.DataFrame, app_settings: Settings | None = None
) -> pl.DataFrame:
    """Clean Open-Meteo current + hourly forecast rows."""
    if df.is_empty():
        return df
    df = df.with_columns(_as_utc("timestamp", df.schema.get("timestamp")).alias("timestamp"))
    valid = (
        pl.col("station_icao").is_not_null()
        & pl.col("timestamp").is_not_null()
        & pl.col("temperature_c").is_not_null()
        & pl.col("temperature_c").is_between(-80.0, 60.0)
    )
    df, _ = _split_quarantine(
        df,
        valid,
        "weather_forecast",
        "invalid station/timestamp or temperature outside [-80, 60]",
        app_settings,
    )
    if df.is_empty():
        return df
    return df.unique(subset=["station_icao", "timestamp"], keep="last")


def transform_weather_taf(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    """Clean TAF (terminal aerodrome forecast) rows from the live collector."""
    if df.is_empty():
        return df
    valid = pl.col("station_icao").is_not_null() & pl.col("raw_taf").is_not_null()
    df, _ = _split_quarantine(df, valid, "weather_taf", "missing station or raw TAF", app_settings)
    if df.is_empty():
        return df
    return df.unique(subset=["station_icao", "issue_time"], keep="last")


def transform_positions(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    """Clean live aircraft positions and guarantee the derived columns.

    The collector classifies positions before Kafka, but Bronze can also hold
    older or externally-published rows. Silver re-derives the operational class
    and carbon estimate here so every downstream layer sees the same schema.
    """
    if df.is_empty():
        return df
    lat = pl.col("latitude")
    lon = pl.col("longitude")
    valid = (
        pl.col("icao24").is_not_null()
        & lat.is_not_null()
        & lon.is_not_null()
        & lat.is_between(-90.0, 90.0)
        & lon.is_between(-180.0, 180.0)
    )
    df, _ = _split_quarantine(
        df, valid, "positions", "invalid icao24 or out-of-range coordinates", app_settings
    )
    if df.is_empty():
        return df
    df = _ensure_position_fields(df)
    return df.unique(subset=["icao24"], keep="last")


_POSITION_CONTEXT = ("callsign", "aircraft_type", "registration", "category")


def _ensure_position_fields(df: pl.DataFrame) -> pl.DataFrame:
    """Add classification + carbon columns derived from services.enrichment."""
    from services.enrichment import enrich_position

    for column in (*_POSITION_CONTEXT, "icao24"):
        if column not in df.columns:
            df = df.with_columns(pl.lit(None).alias(column))

    enriched = [enrich_position(row) for row in df.to_dicts()]
    return pl.from_dicts(enriched, infer_schema_length=None)


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


def transform_aircraft(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    if df.is_empty():
        return df
    valid = (
        pl.col("type_icao").is_not_null()
        & pl.col("manufacturer").is_not_null()
        & pl.col("capacity").is_not_null()
    )
    df, _ = _split_quarantine(
        df, valid, "aircraft", "missing type, manufacturer, or capacity", app_settings
    )
    if df.is_empty():
        return df
    # Validate capacity is positive
    df = df.filter(pl.col("capacity") > 0)
    return df.unique(subset=["type_icao"], keep="last")


def transform_routes(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    if df.is_empty():
        return df
    valid = (
        pl.col("origin").is_not_null()
        & pl.col("destination").is_not_null()
        & (pl.col("origin") != pl.col("destination"))
    )
    df, _ = _split_quarantine(
        df, valid, "routes", "missing origin/destination or same origin/dest", app_settings
    )
    if df.is_empty():
        return df
    return df.unique(subset=["airline", "origin", "destination"], keep="last")


def transform_emissions(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    if df.is_empty():
        return df
    valid = pl.col("aircraft_type").is_not_null() & pl.col("co2_kg_per_hour").is_not_null()
    df, _ = _split_quarantine(
        df, valid, "emissions", "missing aircraft_type or co2 data", app_settings
    )
    if df.is_empty():
        return df
    return df.unique(subset=["aircraft_type"], keep="last")


def transform_notams(df: pl.DataFrame, app_settings: Settings | None = None) -> pl.DataFrame:
    if df.is_empty():
        return df
    valid = pl.col("notam_id").is_not_null() & pl.col("icao_location").is_not_null()
    df, _ = _split_quarantine(
        df, valid, "notams", "missing notam_id or icao_location", app_settings
    )
    if df.is_empty():
        return df
    return df.unique(subset=["notam_id"], keep="last")


_TRANSFORMS: dict[str, Callable[[pl.DataFrame, Settings | None], pl.DataFrame]] = {
    "flights": transform_flights,
    "weather": transform_weather,
    "weather_forecast": transform_weather_forecast,
    "weather_taf": transform_weather_taf,
    "positions": transform_positions,
    "holidays": transform_holidays,
    "fuel": transform_fuel,
    "aircraft": transform_aircraft,
    "routes": transform_routes,
    "emissions": transform_emissions,
    "notams": transform_notams,
}


def _bronze_files(source: str, app_settings: Settings | None = None) -> list[Path]:
    """Bronze Parquet files for a source, oldest first (names are timestamps)."""
    s = app_settings or settings
    source_dir = s.bronze_dir / "parquet" / source
    if not source_dir.exists():
        return []
    return sorted(source_dir.glob("*.parquet"))


def _watermark_path(app_settings: Settings | None = None) -> Path:
    s = app_settings or settings
    return s.checkpoint_dir / "silver_watermarks.json"


def _read_watermarks(app_settings: Settings | None = None) -> dict[str, str]:
    try:
        return json.loads(_watermark_path(app_settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_watermarks(marks: dict[str, str], app_settings: Settings | None = None) -> None:
    path = _watermark_path(app_settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(marks, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read_bronze(
    source: str, app_settings: Settings | None = None, since: str | None = None
) -> pl.DataFrame:
    """Read Bronze Parquet for a source, optionally only files newer than ``since``."""
    files = _bronze_files(source, app_settings)
    if since:
        files = [path for path in files if path.name > since]
    if not files:
        return pl.DataFrame()
    frames = [pl.read_parquet(path) for path in files]
    frames = [frame for frame in frames if not frame.is_empty()]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def run(source: str, app_settings: Settings | None = None, incremental: bool = True) -> int:
    """Run the silver transform for one source. Returns rows processed.

    Incremental by default: only Bronze files written since the last successful
    run are read (tracked in ``checkpoints/silver_watermarks.json``). The
    watermark advances only after the Silver output is safely on disk, so a
    crash reprocesses that window (and dedup makes it idempotent).
    """
    s = app_settings or settings
    files = _bronze_files(source, s)
    marks = _read_watermarks(s) if incremental else {}
    since = marks.get(source)
    new_files = [path for path in files if not since or path.name > since]
    if not new_files:
        logger.info("[silver] source=%s no new bronze files", source)
        return 0
    newest = new_files[-1].name
    df = _read_bronze(source, s, since=since)
    if df.is_empty():
        return 0
    logger.info("[silver] source=%s new files=%s raw rows=%s", source, len(new_files), df.height)

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
        marks[source] = newest
        _write_watermarks(marks, s)
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
        "weather_forecast": ["station_icao", "timestamp"],
        "weather_taf": ["station_icao", "issue_time"],
        "positions": ["icao24"],
        "holidays": ["country", "date"],
        "fuel": ["date", "region"],
        "aircraft": ["type_icao"],
        "routes": ["airline", "origin", "destination"],
        "emissions": ["aircraft_type"],
        "notams": ["notam_id"],
    }.get(source)
    path = _write_partitioned(clean, source, dedup_on=dedup_keys, app_settings=s)
    marks[source] = newest
    _write_watermarks(marks, s)
    logger.info("[silver] source=%s clean rows=%s → %s", source, clean.height, path)
    return clean.height


def run_all(
    sources: list[str] | None = None,
    app_settings: Settings | None = None,
    incremental: bool = True,
) -> dict[str, int]:
    from ingestion.registry import available

    # Registered batch collectors plus transform-only sources that arrive via
    # Kafka/Bronze (positions, weather_taf) so Silver always normalises them.
    targets = sources or sorted(set(available()) | set(_TRANSFORMS))
    results: dict[str, int] = {}
    for source in targets:
        results[source] = run(source, app_settings, incremental=incremental)
    return results


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Silver transform (incremental by default)")
    parser.add_argument("--full", action="store_true", help="reprocess all Bronze files")
    args = parser.parse_args()
    logger.info("Silver results: %s", run_all(incremental=not args.full))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
