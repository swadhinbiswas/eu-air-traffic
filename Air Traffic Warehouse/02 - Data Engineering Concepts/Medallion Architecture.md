[« Back to Index](../00%20-%20Index.md)

# Medallion Architecture

## Overview

The **Medallion Architecture** (Bronze → Silver → Gold) is a data organization pattern popularized by Databricks for organizing data lakes with progressive quality improvement.

```text
┌────────────────────────────────────────────────────┐
│                 Medallion Architecture                │
│                                                      │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐   │
│  │  Bronze  │ ──→ │  Silver  │ ──→ │   Gold   │   │
│  │  (Raw)   │     │(Validated)│    │(Business) │   │
│  └──────────┘     └──────────┘     └──────────┘   │
│                                                      │
│  Quality: Low → Medium → High                        │
│  Structure: Raw → Cleaned → Aggregated               │
│  Users: DEs → Analysts → Business                    │
└────────────────────────────────────────────────────┘
```

## Bronze Layer — The "Landing Zone"

### Characteristics
- **Representation**: Exact copy of source data
- **Transformations**: None (zero processing)
- **Schema**: Source schema preserved as-is
- **Immutable**: Never overwritten, append-only
- **Retention**: Limited (raw data replayed if needed)
- **Users**: Data engineers only

### Rules
```
1. No data transformations
2. Preserve source field names and types
3. Append-only (no updates, no deletes)
4. Store ingestion timestamp (ETL metadata, not source column)
5. Partition by ingestion date
```

### Example Structure
```
bronze/
├── flights/
│   ├── 2024/01/01/   ← One folder per day of ingestion
│   │   └── ingestion_20240101T120000UTC.parquet
│   └── 2024/01/02/
│       └── ingestion_20240102T120000UTC.parquet
├── weather/
├── fuel/
└── holidays/
```

### Bronze Code Pattern
```python
def load_to_bronze(raw_data: pl.DataFrame, source: str, ingestion_date: str):
    """Store raw data in Bronze layer. No transformations."""
    path = f"bronze/{source}/{ingestion_date}/ingestion_{ingestion_date}.parquet"
    raw_data.write_parquet(path)           # As-is, zero processing
    upload_to_hugging_face(path)           # Persist to HF
    log.info(f"Bronze: {source} | {raw_data.height} rows stored at {path}")
```

## Silver Layer — The "Validated Zone"

### Characteristics
- **Source**: Bronze data
- **Transformations**: Clean, deduplicate, standardize
- **Schema**: Normalized, validated schema
- **Idempotent**: Re-running produces identical output
- **Users**: Data engineers + analysts

### Transformations Applied
```
1. Remove duplicates (by business key)
2. Normalize timestamps to UTC
3. Validate airport codes (IATA 3-char pattern)
4. Handle NULL values (defaulting or flagging)
5. Enforce data types (e.g., delay_minutes = INTEGER, not STRING)
6. Filter out corrupted records → quarantine
7. Standardize string casing (airport_code = UPPER)
```

### Example Structure
```
silver/
├── flights/
│   ├── 2024/01/      ← Partitioned by month (not day)
│   │   └── flights_202401.parquet
│   └── 2024/02/
├── weather/
├── airport_metadata/
└── fuel_prices/
```

### Silver Code Pattern
```python
def bronze_to_silver(bronze_path: str, source: str):
    """Clean and validate Bronze data → Silver."""
    raw = pl.scan_parquet(bronze_path)

    silver = (
        raw
        # 1. Deduplicate by business key
        .unique(subset=["flight_id", "date"], keep="last")
        # 2. Normalize timestamps to UTC
        .with_columns([
            pl.col("scheduled_departure")
                .str.to_datetime()
                .dt.convert_time_zone("UTC"),
            pl.col("actual_departure")
                .str.to_datetime()
                .dt.convert_time_zone("UTC"),
        ])
        # 3. Validate business rules
        .filter(
            (pl.col("delay_minutes") >= 0) &
            (pl.col("airport_code").str.len_bytes() == 3) &
            (pl.col("actual_arrival") > pl.col("actual_departure"))
        )
        # 4. Cast types
        .with_columns([
            pl.col("delay_minutes").cast(pl.Int32),
            pl.col("airport_code").str.to_uppercase(),
        ])
        .collect()
    )

    month = extract_month(silver["date"].min())
    output_path = f"silver/{source}/{month}/"
    silver.write_parquet(output_path)

    log.info(f"Silver: {source} | {silver.height} clean rows | "
             f"{raw.collect().height - silver.height} rows discarded")
```

## Gold Layer — The "Business Zone"

### Characteristics
- **Source**: Silver data
- **Aggregated**: Business metrics calculated
- **Star Schema**: Fact and dimension tables
- **Time-enriched**: Date dimension joined, seasonal flags added
- **Users**: Analysts, business, dashboards

### Transformations Applied
```
1. Calculate delay metrics (avg delay, delay category)
2. Calculate distance (haversine between airports)
3. Calculate flight duration (actual_arrival - actual_departure)
4. Join weather data to flights
5. Add time features (hour of day, day of week, month, quarter)
6. Create fact and dimension tables
7. Apply surrogate keys
```

### Output Tables
```
gold/
├── fact_flights/
│   └── partitioned by date_key/
├── fact_delays/
├── dim_airport/
├── dim_airline/
├── dim_weather/
├── dim_date/
├── dim_fuel/
├── airport_metrics/
├── airline_rankings/
├── delay_analysis/
├── weather_impact/
└── seasonal_trends/
```

### Gold Code Pattern
```python
def silver_to_gold(silver_path: str, airport_dim: pl.DataFrame):
    """Build star-schema tables from Silver data."""
    flights = pl.scan_parquet(silver_path)
    airport_dim_lazy = pl.LazyFrame(airport_dim)

    # Generate surrogate keys
    fact_flights = (
        flights
        .join(airport_dim_lazy.select(["airport_key", "airport_code"]),
              left_on="departure_airport", right_on="airport_code",
              how="left")
        .with_columns([
            pl.col("airport_key").alias("departure_airport_key"),
        ])
        .drop(["airport_code_right", "departure_airport"])
        # Calculate metrics
        .with_columns([
            (pl.col("actual_arrival") - pl.col("actual_departure"))
                .dt.total_minutes().alias("flight_duration_min"),
            pl.when(pl.col("status") == "C").then(True).otherwise(False)
                .alias("is_cancelled"),
        ])
        .collect()
    )

    fact_flights.write_parquet("gold/fact_flights/", partition_by="date_key")
```

## Benefits of Medallion Architecture

| Benefit | How Achieved |
|---------|-------------|
| **Reproducibility** | Bronze immutable → pipeline replayable from raw at any time |
| **Data quality progression** | Bronze (raw) → Silver (validated) → Gold (business-ready) |
| **Separation of concerns** | DEs own Bronze/Silver; Analysts own Gold; Clear boundaries |
| **Debugging** | If Gold has bad data, check Silver. If Silver has gaps, check Bronze |
| **Cost optimization** | Bronze has short retention (raw replayable); Gold kept indefinitely |
| **Incremental processing** | Only new Bronze files → only those rows flow through Silver → Gold |

## Retention Strategy

| Layer | Retention | Rationale |
|-------|-----------|-----------|
| Bronze | 30 days | Raw data replayable from source APIs; transient landing zone |
| Silver | Indefinitely | Clean data is valuable; source of truth if Bronze expired |
| Gold | Indefinitely | Business analytics; dashboards consume; never delete |

## Quarantine Handling

```python
# During validation, split:
#   → Silver (valid records)
#   → Quarantine (invalid records)

valid, invalid = split_by_validation(raw)

valid.collect().write_parquet("silver/flights/")
invalid.collect().write_parquet("quarantine/flights/flights_invalid_202401.parquet")

# Invalid record details stored for debugging
log.warning(f"Quarantine: {invalid.height} invalid rows → quarantine/flights/")
```

## Medallion in Data Pipelines

```text
[Source API]
    │
    ▼
[Extract] ─────────────────────────────────
    │                                       │
    ▼                                       │
[Bronze: write Parquet raw]                 │
    │                                       │
    ▼                                       │
[Silver: dedup + validate + normalize]      │
    │                                       │
    ▼                                       │
[Gold: enrich + calculate metrics + star]   │
    │                                       │
    ▼                                       │
[dbt: create analytics views]               │
    │                                       │
    ▼                                       │
[Superset: serve dashboard queries]         │
```

## Industry Adoption

| Company | Medallion Usage |
|---------|----------------|
| Databricks | Originated the term; Bronze = landing, Silver = validated, Gold = analytics |
| Netflix | Similar pattern: "Landing → Conformed → Semantic" |
| Spotify | "Raw → Standardized → Curated" datasets |
| Airbnb | "Source → Event → Domain" data layers |

Medallion architecture is now an industry standard for data lakehouse designs.

---
[« Back to Index](../00%20-%20Index.md)
