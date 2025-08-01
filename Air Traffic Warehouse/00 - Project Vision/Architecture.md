[« Back to Index](../00%20-%20Index.md)

# Architecture

## High-Level Architecture

```text
External Data Sources
    │
    ├── Flight APIs
    ├── Weather APIs
    ├── Fuel APIs
    └── Holiday APIs

            │

            ▼

    FastAPI Ingestion Service
    (REST API endpoints for triggering ingestion)

            │

            ▼

      Raw Storage Layer
    (Hugging Face Datasets — Bronze)

            │

            ▼

       ETL Pipeline
    (Polars for transformation + DuckDB for SQL analytics)

            │

            ▼

     Warehouse Layer
    (Star Schema — Silver + Gold tables in Parquet)

            │

            ▼

         dbt Models
    (Analytics transformations, tests, documentation)

            │

            ▼

     Analytics Dashboard
    (Apache Superset connected to DuckDB)

            │

            ▼

     Orchestration Layer
    (GitHub Actions — scheduled every 6 hours)
```

## Layer Descriptions

### Layer 1: Ingestion Service (FastAPI)
- REST API endpoints to trigger data collection
- Each ingestion module (flights, weather, airports, holidays, fuel) is independent
- Returns status, row count, and any errors
- Auth via API keys stored in GitHub Secrets

### Layer 2: Raw Storage (Hugging Face Datasets — Bronze)
- Immutable raw data preserved exactly as received from sources
- Parquet format with Snappy compression
- Organized by source and ingestion date
- Versioned using Hugging Face Dataset cards

### Layer 3: ETL Pipeline (Polars + DuckDB)
- **Extract**: Read bronze Parquet files
- **Transform**: Clean, validate, deduplicate with Polars lazy DataFrames
- **Load**: Write silver Parquet, generate gold metrics, populate warehouse tables

### Layer 4: Warehouse (Star Schema)
- Fact tables: `fact_flights`, `fact_delays`
- Dimension tables: `dim_airport`, `dim_airline`, `dim_weather`, `dim_date`, `dim_fuel`
- Stored as Parquet, queried via DuckDB
- Partitioned by date

### Layer 5: dbt Models
- Transformations expressed as SQL models
- Built-in testing (unique keys, not null, referential integrity)
- Documentation auto-generated from model descriptions

### Layer 6: Dashboard (Apache Superset)
- Connected to DuckDB via SQLAlchemy
- Four dashboard categories: Executive, Airport, Weather, Airline
- Auto-refresh capability

### Layer 7: Orchestration (GitHub Actions)
- Scheduled workflow triggers every 6 hours
- Sequential pipeline: Fetch → Store → Transform → Load → dbt → Refresh Dashboard
- Failure notifications via GitHub Issues

## Data Flow Detail

```text
[GitHub Actions trigger]

        │
        ▼

[FastAPI Ingestion Endpoint]
  ├── /api/flights      → Raw flights data
  ├── /api/weather      → Raw weather data
  ├── /api/airports     → Airport metadata
  ├── /api/holidays     → Holiday calendar
  └── /api/fuel         → Fuel prices

        │
        ▼

[Bronze Storage — Hugging Face]
  flights/YYYY-MM/
  weather/YYYY-MM/
  airports/latest/
  holidays/YYYY/
  fuel/YYYY-MM/

        │
        ▼

[Silver — Polars Transformation]
  → Deduplication
  → Timestamp normalization (UTC)
  → Airport code validation
  → Null handling
  → Record validation

        │
        ▼

[Gold — Polars + DuckDB]
  → Delay metrics calculation
  → Distance calculation
  → Duration calculation
  → Cancellation flags
  → Seasonal features

        │
        ▼

[Star Schema Warehouse — Parquet]
  → fact_flights partitioned by date
  → dim_airport, dim_airline, dim_weather, dim_date, dim_fuel

        │
        ▼

[dbt Models]
  → airport_metrics
  → airline_rankings
  → delay_analysis
  → weather_impact
  → seasonal_trends

        │
        ▼

[Superset Dashboard]
  → Executive Dashboard
  → Airport Dashboard
  → Weather Dashboard
  → Airline Dashboard
```

## Component Diagram

```text
┌─────────────────────────────────────────────────────┐
│                    GitHub Actions                     │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌────────┐ │
│  │ Extract │→│Transform│→│  Load   │→│  dbt   │ │
│  └─────────┘  └─────────┘  └─────────┘  └────────┘ │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                 Hugging Face Datasets                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐             │
│  │ Bronze  │→│ Silver  │→│  Gold   │             │
│  └─────────┘  └─────────┘  └─────────┘             │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│           Analytics Layer (DuckDB + Superset)          │
│  ┌─────────────┐  ┌───────────────┐                 │
│  │ DuckDB SQL  │→│  Superset UI  │                 │
│  └─────────────┘  └───────────────┘                 │
└─────────────────────────────────────────────────────┘
```

## Docker Service Topology

```yaml
services:
  api:             # FastAPI ingestion service (port 8000)
  superset:        # Apache Superset dashboard (port 8088)
  duckdb:          # DuckDB embedded in API (no separate service)
  dbt:             # dbt runner container (one-shot)
  scheduler:       # Simulated cron for local dev (GitHub Actions in prod)
```

## Technology Decision Rationale

| Decision | Why |
|----------|-----|
| DuckDB over PostgreSQL | Embedded OLAP engine, no server management, fast on Parquet |
| Polars over Pandas | 10-30x faster, lazy evaluation, lower memory, Rust core |
| Hugging Face over S3 | Free, public by default, versioned, avoids AWS lock-in |
| GitHub Actions over Airflow | Free CI minutes, no infra, easy reproducibility |
| dbt-core over custom SQL | Industry standard, built-in testing, documentation generation |
| FastAPI over Flask | Async support, automatic OpenAPI docs, modern Python |
| Superset over Metabase | More SQL-native, better Docker support, dbt-compatible |

## Network Topology

```text
Internet
    │
    ▼
FastAPI (0.0.0.0:8000)
    │
    ├──→ Hugging Face Hub (storage API)
    │       └──→ Bronze / Silver / Gold Parquet files
    │
    ├──→ External APIs
    │       ├── Flight data APIs
    │       ├── Weather APIs
    │       ├── Fuel price APIs
    │       └── Holiday APIs
    │
    └──→ DuckDB (in-process, no network)
            └──→ Reads Parquet from Hugging Face
            └──→ Queries served to Superset
```

## Failure Modes & Mitigations

| Failure | Mitigation |
|---------|------------|
| API unavailable | Exponential backoff retry (3 attempts) |
| Invalid data | Reject to quarantine, continue processing valid records |
| DuckDB file corrupted | Rebuild from Parquet (immutable source) |
| GitHub Actions timeout | Split pipeline into sequential jobs |
| Hugging Face rate limit | Cache locally, batch upload |
| Out of memory | Polars streaming + lazy evaluation |
| Schema drift | Schema validation at ingestion boundary |

---
[« Back to Index](../00%20-%20Index.md)
