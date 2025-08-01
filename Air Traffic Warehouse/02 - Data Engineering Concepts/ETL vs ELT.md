[« Back to Index](../00%20-%20Index.md)

# ETL vs ELT

## Definitions

| | ETL | ELT |
|---|-----|-----|
| **Order** | Extract → Transform → Load | Extract → Load → Transform |
| **Where transform happens** | Outside the warehouse (external engine) | Inside the warehouse (SQL engine) |
| **Typical tool** | Polars, Pandas, Spark | dbt, warehouse SQL |
| **Data volume** | Pre-cloud era (smaller data) | Cloud era (massive data) |
| **Storage** | Limited staging area | Cheap cloud storage |

## ETL (Extract → Transform → Load)

```
[Source] → [Python/Polars transforms] → [Load clean data to warehouse]
```

### Advantages
- **Data quality gate before warehouse**: Only clean data enters
- **Reduced warehouse storage**: Junk filtered out upstream
- **Platform independence**: Transform logic runs anywhere

### Disadvantages
- **Can't leverage warehouse compute**: Transforming outside
- **Raw data lost**: No original source preserved (unless explicitly saved)
- **Slower for large data**: Python/Spark slower than warehouse SQL

## ELT (Extract → Load → Transform)

```
[Source] → [Load raw to warehouse] → [SQL/dbt transforms inside warehouse]
```

### Advantages
- **Raw data preserved**: Bronze layer always exists
- **Leverage warehouse power**: Columnar engines like DuckDB/Redshift are fast
- **Reprocess from raw**: Always can re-transform if logic changes
- **dbt compatible**: dbt is built for ELT

### Disadvantages
- **Warehouse costs for junk data**: Raw storage costs money (less relevant with free Hugging Face)
- **Data quality after load**: Bad data enters warehouse first
- **Vendor lock-in**: Transform SQL is warehouse-specific

## This Project's Approach: Hybrid ETL (Bronze → Silver → Gold)

We use a **Medallion architecture with hybrid processing**:

```
[Source API]
    │
    ▼
[Extract + Load to Bronze]           ← ELT: Raw data stored immediately (Hugging Face)
    │
    ▼
[Transform to Silver with Polars]    ← ETL: Python validation, dedup, cleanup
    │
    ▼
[Transform to Gold with Polars]      ← ETL: Python metrics, joins, enrichment
    │
    ▼
[Load to Warehouse with DuckDB]      ← ELT: SQL aggregations, dbt models
    │
    ▼
[dbt models for analytics views]     ← ELT: Pure SQL transformations in warehouse
```

### Why Hybrid?

| Step | Approach | Rationale |
|------|----------|-----------|
| Bronze | ELT (load raw immediately) | Preserve source data; immutable |
| Silver | ETL (Polars) | Complex validation logic; Python is expressive |
| Gold | ETL/ELT (Polars + DuckDB) | Metrics in Python, aggregations in SQL |
| dbt | ELT (DuckDB SQL) | Leverage OLAP engine for final analytics |

## Comparison: Same Transformation in Each Pattern

### ETL Pattern (Polars — this project's Silver)
```python
import polars as pl

raw = pl.scan_parquet("bronze/flights/*.parquet")

silver = (
    raw
    .unique(subset=["flight_id", "date"])
    .with_columns(
        pl.col("scheduled_departure").str.to_datetime().dt.convert_time_zone("UTC"),
        pl.col("actual_departure").str.to_datetime().dt.convert_time_zone("UTC"),
    )
    .filter(pl.col("delay_minutes") >= 0)
    .filter(pl.col("airport_code").str.len_bytes() == 3)
    .collect()
)
silver.write_parquet("silver/flights/")
```

### ELT Pattern (dbt — this project's analytics views)
```sql
-- dbt model: gold.airline_rankings.sql
WITH cleaned AS (
    SELECT * FROM {{ ref('fact_flights') }}
    WHERE delay_minutes >= 0
),
ranked AS (
    SELECT
        airline_key,
        COUNT(*) AS flight_count,
        AVG(delay_minutes) AS avg_delay,
        RANK() OVER (ORDER BY AVG(delay_minutes) ASC) AS performance_rank
    FROM cleaned
    GROUP BY airline_key
)
SELECT
    r.*,
    da.airline_code,
    da.airline_name
FROM ranked r
JOIN {{ ref('dim_airline') }} da ON r.airline_key = da.airline_key
```

## Decision Rules for ETL vs ELT

```text
Is the transformation simple SQL aggregation?
    → ELT (dbt, warehouse SQL)

Is the transformation complex (parsing, normalization, fuzzy matching)?
    → ETL (Polars, Python)

Do you need to preserve raw source data?
    → ELT (load to Bronze first)

Is the data volume massive (terabytes)?
    → ELT (leverage warehouse MPP)

Do you want transformation code to be portable (not locked to one warehouse)?
    → ETL (Polars runs anywhere)
```

## Historical Context

| Era | Pattern | Why |
|-----|---------|-----|
| 1990s–2000s | ETL | Disk expensive, warehouse compute expensive; IBM Datastage, Informatica |
| 2010s–now | ELT | Cloud cheap (S3), warehouse MPP (Redshift, BigQuery), dbt popularized |
| This project | Hybrid | We have both expressive Python (Polars) and powerful SQL (DuckDB) |

---
[« Back to Index](../00%20-%20Index.md)
