[« Back to Index](../00%20-%20Index.md)

# Data Warehouse

## Definition

A **Data Warehouse** is a subject-oriented, integrated, time-variant, and non-volatile collection of data organized to support management decision-making.

(Bill Inmon's original definition)

## Key Characteristics

| Characteristic | Meaning | In This Project |
|---------------|---------|-----------------|
| **Subject-Oriented** | Organized by business domain | Aviation (flights, airports, airlines, weather) |
| **Integrated** | Data from multiple sources unified | Flights + Weather + Fuel + Holidays → One warehouse |
| **Time-Variant** | Historical snapshots preserved | Every 6-hour run adds to fact tables; date dimension tracks history |
| **Non-Volatile** | Data not deleted, only appended | Bronze immutable; facts accumulated; occasional Type 1 SCD overwrites |

## Warehouse Design Approaches

### Kimball (Star Schema) — We Use This

```text
Fact tables (measurements) surrounded by Dimension tables (context)

      dim_date    dim_airport
          │          │
          └─── fact_flights ────┘
                    │
          dim_airline    dim_weather
```

**Focus**: Fast queries, user-friendly, denormalized dimensions.
**Creator**: Ralph Kimball.
**Best for**: Reporting, dashboards, ad-hoc analytics (our use case).

### Inmon (3NF Data Warehouse)

```text
Normalized enterprise-wide data model, then departmental data marts
```

**Focus**: Enterprise integration, normalization, single source of truth.
**Creator**: Bill Inmon.
**Best for**: Large enterprises with multiple source systems (overkill for this project).

### Data Vault

```text
Hubs (business keys) + Links (relationships) + Satellites (descriptive attributes)
```

**Focus**: Auditability, historical tracking, agile schema evolution.
**Creator**: Dan Linstedt.
**Best for**: Complex source integrations, teams needing parallel development.

## Why We Chose Kimball Star Schema

| Reason | Detail |
|--------|--------|
| **Query simplicity** | `SELECT ... FROM fact_flights JOIN dim_airline` — users understand it |
| **Dashboard performance** | Denormalized dimensions = fewer joins = faster |
| **dbt compatibility** | dbt excels at building star schemas |
| **Project scope** | ~5 sources, manageable; no need for Data Vault complexity |
| **Recruiter familiarity** | Star schema is the standard interview topic |

## Warehouse Layers in This Project

```
┌────────────────────────────────────────────┐
│            Data Warehouse                    │
│                                              │
│  Bronze  —  Raw landing zone (Parquet)     │
│                                              │
│  Silver  —  Cleaned staging (Parquet)      │
│                                              │
│  Gold    —  Business models (Parquet + SQL) │
│     ├── fact_flights                        │
│     ├── fact_delays                         │
│     ├── dim_airport                         │
│     ├── dim_airline                         │
│     ├── dim_weather                         │
│     ├── dim_date                            │
│     └── dim_fuel                            │
│                                              │
│  Analytics — dbt models (materialized views)│
│     ├── airport_metrics                     │
│     ├── airline_rankings                    │
│     ├── delay_analysis                      │
│     ├── weather_impact                      │
│     └── seasonal_trends                     │
└────────────────────────────────────────────┘
```

## Loading Patterns

| Pattern | When to Use | In This Project |
|---------|-------------|-----------------|
| **Full Load** | Initial setup, small dimension tables | Airport dim (initial), Airline dim |
| **Incremental Load** | Growing fact tables, daily batches | Fact tables: only new/modified flights |
| **SCD Type 1** | Overwrite changed attributes | Airline name change, Airport code change |
| **SCD Type 2** | Track history of changes | Not needed (low churn in aviation dims) |

## Incremental Loading Logic

```python
# Check last processed timestamp from checkpoint
checkpoint = load_checkpoint()

new_data = (
    pl.scan_parquet("bronze/flights/**/*.parquet")
    .filter(pl.col("extracted_at") > checkpoint["last_flight_timestamp"])
    .collect()
)

# Append to existing fact table
append_to_fact_table(new_data)

# Update checkpoint
update_checkpoint({"last_flight_timestamp": new_data["extracted_at"].max()})
```

## Data Quality in the Warehouse

| Check | Layer | Implementation |
|-------|-------|---------------|
| Non-negative delays | Silver/Gold | `WHERE delay_minutes >= 0` |
| Valid IATA codes (3 chars) | Silver | `len(airport_code) == 3` |
| Arrival after departure | Silver | `actual_arrival > actual_departure` |
| Realistic weather ranges | Silver | `-80 <= temp <= 60` |
| No duplicate flight_ids | Silver | `.unique(subset=["flight_id", "date"])` |
| Referential integrity | Gold | All foreign keys exist in dim tables |

## Warehouse Performance Optimization

| Technique | Benefit | Implementation |
|-----------|---------|---------------|
| Columnar storage (Parquet) | Read only needed columns | All warehouse layers = Parquet |
| Partition pruning | Skip irrelevant date folders | `gold/fact_flights/YYYY/MM/` |
| Compression (Zstd) | 70–90% size reduction | Parquet compression = Zstd |
| DuckDB in-process | No network latency | Embedded engine |
| Pre-aggregations (Gold) | Pre-computed KPIs | `gold/*` tables = derived metrics |

## Warehouse Query Examples

### Simple: Top airports by delay
```sql
SELECT
    da.airport_code,
    da.airport_name,
    ROUND(AVG(ff.delay_minutes), 1) AS avg_delay,
    COUNT(*) AS total_flights
FROM fact_flights ff
JOIN dim_airport da ON ff.departure_airport_key = da.airport_key
GROUP BY da.airport_code, da.airport_name
HAVING COUNT(*) > 1000
ORDER BY avg_delay DESC
LIMIT 10;
```

### Complex: Weather-delay matrix
```sql
SELECT
    dw.condition AS weather_condition,
    dd.year,
    dd.month,
    COUNT(*) AS flights,
    ROUND(AVG(ff.delay_minutes), 1) AS avg_delay,
    ROUND(SUM(CASE WHEN ff.cancelled THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS cancellation_pct
FROM fact_flights ff
JOIN dim_weather dw ON ff.weather_key = dw.weather_key
JOIN dim_date dd ON ff.date_key = dd.date_key
GROUP BY dw.condition, dd.year, dd.month
ORDER BY dd.year, dd.month, avg_delay DESC;
```

## Tools Comparison for Warehouse Engine

| Tool | Pros | Cons | Our Choice? |
|------|------|------|-------------|
| **DuckDB** | Embedded, no server, fast on Parquet, free | Not multi-user, not distributed | ✅ **Yes** |
| ClickHouse | Real-time OLAP, petabyte scale | Requires server management, Docker complexity | No (overkill) |
| PostgreSQL | Familiar, ACID, multi-user | Row-oriented, slow for analytics | No (OLTP, not OLAP) |
| Redshift/BigQuery | MPP, cloud-native | Paid, proprietary, defeats zero-cost goal | No |
| SQLite | Embedded, zero config | Row-oriented, no Parquet support | No |

---
[« Back to Index](../00%20-%20Index.md)
