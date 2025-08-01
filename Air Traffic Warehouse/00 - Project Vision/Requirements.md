[« Back to Index](../00%20-%20Index.md)

# Requirements

## Functional Requirements

### FR-01: Data Collection
The platform must ingest data from the following sources:
- **Flight data**: flight_id, airline, departure/arrival airports, scheduled vs actual times, delays, status
- **Airport metadata**: airport code, name, city, country, coordinates, timezone
- **Weather conditions**: temperature, humidity, wind speed, visibility, condition type, timestamp
- **Fuel prices**: country-level jet fuel prices per date
- **Public holidays**: country-level holiday calendar

### FR-02: Data Processing
- Validate all incoming records against defined rules
- Remove duplicate records
- Standardize timestamps to UTC
- Handle missing values with documented strategies
- Generate derived business metrics (delay minutes, distance, duration)
- Create warehouse tables following star schema design

### FR-03: Medallion Architecture
- **Bronze Layer**: Raw immutable data; preserve source schema; no transformations
- **Silver Layer**: Cleaned datasets; deduplicated, validated, timestamp-normalized
- **Gold Layer**: Business-ready datasets with calculated metrics

### FR-04: Incremental Loading
- Only process new/modified records since last run
- Store checkpoint metadata (`last_run.json`, `checkpoint.json`)
- Query pattern: `WHERE updated_at > last_processed_timestamp`

### FR-05: Analytics Support
- Delay analysis by airport, airline, time period, and weather condition
- Airline performance rankings
- Weather impact correlation studies
- Seasonal trend identification
- Airport performance benchmarking

### FR-06: Observability
- ETL execution logs with structured levels (INFO, WARNING, ERROR, CRITICAL)
- Data quality reports after each run
- Pipeline execution time tracking
- Error reports with stack traces
- Dataset size monitoring

### FR-07: Dashboards
- **Executive Dashboard**: Total flights, average delay, cancellation rate, airline count
- **Airport Dashboard**: Top delayed airports, delay heatmap, traffic distribution
- **Weather Dashboard**: Rain/wind/fog impact on delays
- **Airline Dashboard**: Ranking, monthly performance, delay distribution

## Non-Functional Requirements

### NFR-01: Reliability
- Idempotent pipelines (same input always produces same output, safe to re-run)
- Retry mechanisms with exponential backoff
- Graceful degradation when APIs are unavailable
- Checkpoint-based recovery

### NFR-02: Performance
- Query latency under 2 seconds for all dashboard queries
- Efficient columnar storage (Parquet) with Snappy/Zstd compression
- DuckDB for in-process analytical queries (no network overhead)
- Polars lazy evaluation for memory-efficient transformations

### NFR-03: Scalability
- Must process millions of rows without memory issues
- Dataset partitioning by date and region
- Architecture supports future streaming ingestion (Kafka/CDC)
- Storage (Hugging Face) handles growing dataset sizes

### NFR-04: Maintainability
- Modular codebase organized by domain (ingestion, pipelines, warehouse, dbt)
- Automated unit, integration, and end-to-end tests
- Comprehensive documentation in Obsidian vault
- ADRs (Architecture Decision Records) for key technology choices

### NFR-05: Reproducibility
```bash
git clone <repo-url>
make bootstrap   # install dependencies, initialize storage
make run         # execute full pipeline end-to-end
```
Must work on Linux, macOS, and WSL2 Windows.

### NFR-06: Security
- API keys stored in GitHub Secrets (never in code)
- No hardcoded credentials
- Immutable bronze datasets
- Rate limiting on API calls
- Input validation on all ingested data

## Technology Constraints

| Constraint | Rationale |
|------------|-----------|
| No managed cloud data services | Demonstrate self-managed warehouse skills |
| Open-source tools only | Zero cost, reproducible by recruiters |
| Python 3.13+ | Latest language features |
| Hugging Face Datasets for storage | Free, versioned, accessible |
| GitHub Actions for orchestration | Free CI minutes, no cron server needed |

---
[« Back to Index](../00%20-%20Index.md)
