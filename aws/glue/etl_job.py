"""Glue ETL job: raw landing zone → curated zone (stage 4 of the AWS version).

Reads the five raw domains the crawler registered in the Glue Data Catalog,
cleans them, and writes Parquet to the curated bucket under ``fact/``, ``dim/``,
``aggregates/`` and ``parquet/`` — which is what Athena queries (stage 6).

The rules are the ones that make the numbers trustworthy, carried over from the
main pipeline (``pipelines/silver.py``, ``pipelines/warehouse.py``):

* **deduplicate** across providers — OpenSky movements and AirLabs schedules
  describe the same flight with different ids, so flights are deduped on
  ``callsign + date + departure_icao + arrival_icao``;
* **unknown stays NULL** — never coerce a missing value to zero;
* only average delays that are actually known;
* every transform is **safe to run twice** (job bookmarks + overwrite by
  partition), because a retry must not double count.

Glue arguments (set by ``infra/terraform-serverless/glue.tf``):
    --RAW_DATABASE       catalog database the crawler filled
    --CURATED_DATABASE   catalog database for the curated tables
    --CURATED_BUCKET     destination bucket
    --CURATED_PREFIX     key prefix (default "curated")

Run locally against a Spark/Glue dev endpoint; it is plain PySpark with the Glue
extensions used only for the catalog sink and the job bookmark.
"""

from __future__ import annotations

import sys

from awsglue.context import GlueContext  # type: ignore[import-not-found]
from awsglue.job import Job  # type: ignore[import-not-found]
from awsglue.utils import getResolvedOptions  # type: ignore[import-not-found]
from pyspark.context import SparkContext  # type: ignore[import-not-found]
from pyspark.sql import DataFrame  # type: ignore[import-not-found]
from pyspark.sql import functions as F  # type: ignore[import-not-found]
from pyspark.sql.window import Window  # type: ignore[import-not-found]

# raw domain → curated destination. ``kind`` selects the curated subfolder, the
# rest is the table name Athena will see.
DOMAIN_TABLES: dict[str, dict[str, str]] = {
    "adsb": {"table": "fact_positions", "kind": "fact"},
    "opensky": {"table": "fact_flights", "kind": "fact"},
    "airlabs": {"table": "fact_schedules", "kind": "fact"},
    "metar": {"table": "weather", "kind": "fact"},
    "eurostat": {"table": "fact_airport_official", "kind": "fact"},
}

# Business key per table; deduping on these is what stops double counting.
DEDUPE_KEYS: dict[str, list[str]] = {
    "fact_flights": ["callsign", "departure_icao", "arrival_icao", "date"],
    "fact_schedules": ["callsign", "departure_icao", "arrival_icao", "date"],
    "fact_positions": ["icao24", "timestamp"],
    "weather": ["station_icao", "timestamp"],
    "fact_airport_official": ["airport_icao", "year", "month"],
}

TIMESTAMP_HINTS = ("time", "date", "collected_at", "updated_at", "_at")


def _args() -> dict[str, str]:
    return getResolvedOptions(
        sys.argv,
        [
            "JOB_NAME",
            "RAW_DATABASE",
            "CURATED_DATABASE",
            "CURATED_BUCKET",
            "CURATED_PREFIX",
        ],
    )


def _read_raw(glue: GlueContext, database: str, domain: str, table_name: str) -> DataFrame | None:
    """Read ``<database>.<table>`` if the crawler registered it, else None."""
    for candidate in (f"{domain}_{table_name}", table_name, domain):
        try:
            frame = glue.create_dynamic_frame.from_catalog(
                database=database, table_name=candidate, transformation_ctx=f"read_{candidate}"
            )
            return frame.toDF()
        except Exception:  # noqa: BLE001 - a missing table is expected early on
            continue
    return None


def _clean(df: DataFrame) -> DataFrame:
    """Trim strings, drop all-null rows, and derive a ``date`` partition."""
    string_cols = [f.name for f in df.schema.fields if str(f.dataType) == "StringType()"]
    for name in string_cols:
        df = df.withColumn(name, F.trim(F.col(name)))

    # Cast anything that looks like a timestamp; leave unparseable values NULL
    # rather than failing the job (``to_timestamp`` yields NULL on bad input).
    for name in df.columns:
        if any(hint in name.lower() for hint in TIMESTAMP_HINTS):
            df = df.withColumn(name, F.to_timestamp(F.col(name)))

    if "date" not in df.columns and "collected_at" in df.columns:
        df = df.withColumn("date", F.to_date(F.col("collected_at")))
    if "date" not in df.columns:
        df = df.withColumn("date", F.to_date(F.current_timestamp()))

    df = df.dropna(how="all")
    # Unknown stays unknown: only fill structural nulls, never measurements.
    return df


def _dedupe(df: DataFrame, table: str) -> DataFrame:
    keys = DEDUPE_KEYS.get(table, [])
    present = [key for key in keys if key in df.columns]
    if not present:
        return df
    # Newest row wins, so a correction to an existing flight is the one kept.
    order = "collected_at" if "collected_at" in df.columns else present[0]
    ranked = df.withColumn(
        "_rank",
        F.row_number().over(Window.partitionBy(*present).orderBy(F.col(order).desc_nulls_last())),
    )
    return ranked.filter(F.col("_rank") == 1).drop("_rank")


def _write_curated(
    glue: GlueContext, df: DataFrame, database: str, table: str, kind: str, bucket: str, prefix: str
) -> None:
    """Write Parquet to the curated zone and update the Glue catalog.

    Partitioned by ``date`` so Athena prunes pruned partitions and a re-run only
    overwrites the days it touched.
    """
    path = f"s3://{bucket}/{prefix}/{kind}/{table}"
    dynamic = glue.create_dynamic_frame.from_options(
        frame=df,
        connection_type="s3",
        connection_options={"path": path, "partitionKeys": ["date"]},
        format="parquet",
        transformation_ctx=f"write_{table}",
    )
    glue.write_dynamic_frame.from_catalog(
        frame=dynamic,
        database=database,
        table_name=table,
        transformation_ctx=f"catalog_{table}",
        additional_options={"enableUpdateCatalog": True, "partitionKeys": ["date"]},
    )
    print(f"[etl] wrote {table} → {path} (dt partitions)")


def _aggregates(dfs: dict[str, DataFrame]) -> DataFrame | None:
    """Daily counts and known-only delay averages, the cheap BI aggregates."""
    flights = dfs.get("fact_flights") or dfs.get("fact_schedules")
    if flights is None:
        return None
    known_delay = F.col("delay_minutes") if "delay_minutes" in flights.columns else F.lit(None)
    agg = flights.groupBy("date").agg(
        F.count("*").alias("flights"),
        F.avg(known_delay).alias("avg_delay_minutes"),
        F.count(known_delay).alias("flights_with_known_delay"),
    )
    return agg


def main() -> None:
    args = _args()
    sc = SparkContext()
    glue = GlueContext(sc)
    job = Job(glue)
    job.init(args["JOB_NAME"], args)

    curated_prefix = args.get("CURATED_PREFIX") or "curated"
    frames: dict[str, DataFrame] = {}

    for domain, spec in DOMAIN_TABLES.items():
        table = spec["table"]
        raw = _read_raw(glue, args["RAW_DATABASE"], domain, table)
        if raw is None:
            print(f"[etl] raw table for {domain!r} not found — skipping")
            continue
        cleaned = _dedupe(_clean(raw), table)
        frames[table] = cleaned
        _write_curated(
            glue,
            cleaned,
            args["CURATED_DATABASE"],
            table,
            spec["kind"],
            args["CURATED_BUCKET"],
            curated_prefix,
        )

    daily = _aggregates(frames)
    if daily is not None:
        _write_curated(
            glue,
            daily,
            args["CURATED_DATABASE"],
            "daily_traffic",
            "aggregates",
            args["CURATED_BUCKET"],
            curated_prefix,
        )

    job.commit()
    print("[etl] done")


if __name__ == "__main__":
    main()
