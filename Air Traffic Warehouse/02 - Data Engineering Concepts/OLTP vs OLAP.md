[« Back to Index](../00%20-%20Index.md)

# OLTP vs OLAP

## Fundamental Difference

| | OLTP | OLAP |
|---|------|------|
| **Full Name** | Online Transaction Processing | Online Analytical Processing |
| **Purpose** | Run the business (transactions) | Understand the business (analytics) |
| **Operations** | INSERT, UPDATE, DELETE (single rows) | SELECT (millions of rows at once) |
| **Data Model** | Normalized (3NF) | Denormalized (Star Schema) |
| **Query Pattern** | `SELECT * FROM flights WHERE flight_id = ?` | `SELECT AVG(delay_minutes) FROM flights WHERE year = 2024 GROUP BY airline` |
| **Storage** | Row-oriented | Column-oriented |
| **Latency** | Milliseconds | Seconds to minutes |
| **Users** | Operational staff, APIs, booking systems | Analysts, managers, dashboards |
| **Examples** | PostgreSQL, MySQL, SQL Server | DuckDB, ClickHouse, Redshift, BigQuery |

## Key Characteristics

### OLTP
- **High volume of small transactions**: Thousands of INSERT/UPDATE per second
- **ACID compliance**: Atomicity, Consistency, Isolation, Durability
- **Concurrency**: Many users writing simultaneously
- **Index strategy**: B-tree indexes on primary keys and foreign keys
- **Example in aviation**: Airline reservation system — booking a seat

### OLAP
- **Low volume of large queries**: Few queries scanning millions of rows
- **Read-heavy**: 99% reads, 1% writes
- **Aggregations**: SUM, AVG, COUNT, Window functions
- **Index strategy**: Partition pruning, columnar compression
- **Example in aviation**: "What was the average delay at JFK in 2024?"

## Row-Oriented vs Column-Oriented Storage

```
Row storage (OLTP):
Flight data:
[ID|code|dpt|arr|time|delay] [ID|code|dpt|arr|time|delay] [ID|code|dpt|arr|time|delay]
 → Reads entire row, fast for single-record

Column storage (OLAP):
[ID|ID|ID] [code|code|code] [dpt|dpt|dpt] [time|time|time] [delay|delay|delay]
 → Reads only needed columns, fast for aggregations
```

## Why We Use OLAP (DuckDB + Parquet) in This Project

We are building a data warehouse, not a booking system:

| Requirement | OLAP Fit |
|-------------|----------|
| "Average delay by airline" | Aggregation over millions of rows |
| "Top delayed airports this month" | GROUP BY + ORDER BY + LIMIT |
| "Weather-delay correlation" | JOIN millions of flights to weather |
| "Seasonal trend over 5 years" | Time-series window functions |
| Query latency < 2s | Columnar engine with partition pruning |

An OLTP database (PostgreSQL) would struggle with these analytical queries at scale. DuckDB is purpose-built for OLAP on local files.

## Dual-Engine Pattern

This project intentionally demonstrates both paradigms:

| Component | Engine | Paradigm |
|-----------|--------|----------|
| Ingestion service | DuckDB (for fast bulk load) | OLAP |
| Transformations | Polars (lazy columnar processing) | OLAP |
| Warehouse queries | DuckDB SQL | OLAP |
| dbt transformations | DuckDB SQL | OLAP |
| Dashboard queries | DuckDB SQL | OLAP |

PostgreSQL is deliberately NOT used, to show that for pure analytics, an embedded OLAP engine is superior.

## When to Use Each (Decision Guide)

```text
Are you building a transactional application (CRUD, forms, many concurrent writes)?
    → OLTP (PostgreSQL, MySQL)

Are you building a reporting system (dashboards, aggregated insights)?
    → OLAP (DuckDB, ClickHouse)

Are you building both?
    → Hybrid: OLTP for writes → ETL → OLAP for reads
```

## Query Comparison: Same Question, Two Engines

### OLTP-style (PostgreSQL) — Inefficient
```sql
-- Row-oriented scan, pulls all columns even though we only need 2
-- Index on airline_code helps but still slow for aggregation
SELECT airline_code, AVG(delay_minutes)
FROM flights
WHERE scheduled_departure BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY airline_code;  -- May take 30+ seconds on 10M rows
```

### OLAP-style (DuckDB on Parquet) — Efficient
```sql
-- Columnar scan, reads only airline_code and delay_minutes columns
-- Partition pruning skips irrelevant date partitions
SELECT airline_code, AVG(delay_minutes)
FROM read_parquet('gold/fact_flights/**/*.parquet')
WHERE date_key BETWEEN 20240101 AND 20241231
GROUP BY airline_code;  -- Under 1 second on 10M rows
```

## Memory Architecture

| OLTP Engine | OLAP Engine |
|-------------|-------------|
| Keeps working set in buffer pool | Columnar compression fits more in RAM |
| Cache hit ratio critical | Sequential scans are expected |
| Random access pattern | Sequential access pattern |
| Index lookups dominate | Full column scans dominate |

## This Project's Architecture Map

```text
[External APIs]              ← OLTP systems (airline reservation, weather stations)
        │
        ▼
[Ingestion → Bronze]         ← Batch fetch, no ACID needed
        │
        ▼
[Silver / Gold Parquet]      ← Columnar storage
        │
        ▼
[DuckDB OLAP queries]        ← Analytical engine
        │
        ▼
[Superset dashboards]        ← Read-only, aggregated views
```

All downstream systems are OLAP. No transactions, no concurrent writes = no need for PostgreSQL.

---
[« Back to Index](../00%20-%20Index.md)
