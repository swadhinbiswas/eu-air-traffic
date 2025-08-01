# Air Traffic Analytics Platform

A batch data platform for European aviation analytics — flight delays, weather correlations, airline benchmarking, and operational KPIs. Built on a Medallion architecture using Polars, DuckDB, dbt, FastAPI, and GitHub Actions. Zero compute cost, fully reproducible.

## Problem Statement

European aviation generates massive volumes of operational data (flights, weather, fuel prices, schedules) but analysing delay patterns, weather impact, and airline performance requires joining data across multiple sources with different schemas, formats, and quality levels. This platform ingests raw data from public APIs, validates and cleans it through a layered pipeline, and produces analytics-ready marts that answer specific business questions:

- Which airports have the worst on-time performance?
- How does weather correlate with delay severity?
- What is the cost impact of delays under EU261/2004?
- How do seasonal patterns affect flight volume and delays?

## Architecture

```
Sources                 Collectors              Medallion Warehouse                Analytics
-----------             -----------             -------------------                ---------
OpenSky          -->    FlightCollector    -->  Bronze (raw JSONL)            -->  DuckDB Star Schema
OpenWeather      -->    WeatherCollector   -->  Silver (clean/validate)       -->  Gold Marts (dbt)
AviationStack    -->    FuelCollector      -->  Gold (analytics marts)        -->  Dashboard (HTML)
OpenFlights      -->    AirportCollector   -->  Quarantine (dead-letter)      -->  API (FastAPI)
Holidays API     -->    HolidayCollector   -->  Checkpoints (watermarks)      -->  HF Hub (versioned)
```

Pipeline flow: `collect -> bronze -> silver -> gold -> warehouse -> quality report -> (optional) HF upload`

### Data Sources

| Source | What | Coverage | Rate Limit |
|--------|------|----------|------------|
| OpenSky Network | Live flight states, arrivals/departures | Global (EU focus) | 10 req/min (anonymous) |
| OpenWeather Map | Current weather per airport station | Global | 60 req/min |
| AviationStack | Fuel prices, airline metadata | Global | 100 req/month (free tier) |
| OpenFlights | Airport database (9,300 airports) | Global | Static CSV |
| Holiday API | Public holidays by country | EU countries | Unlimited |

### Medallion Layers

**Bronze** — Raw data landed as-is from the source APIs. Appended per run, never modified. Stored as JSONL (ingestion) and consolidated Parquet (processing). Checkpoint watermarks prevent re-fetching already-collected windows.

**Silver** — Cleaned, validated, deduplicated. Timestamps normalised to UTC. Bad rows quarantined with reason and timestamp. Natural keys used for idempotent deduplication (e.g., `flight_id`, `station_icao + timestamp`). This is where the documented validation rules are enforced.

**Gold** — Business-ready analytical marts computed from Silver. Six pre-aggregated tables covering airport metrics, airline rankings, delay analysis, weather impact, seasonal trends, and fuel prices. Written as compressed Parquet and registered as DuckDB views for zero-copy querying.

**Quarantine** — Dead-letter queue. Rows failing Silver validation are written to `warehouse/quarantine/<source>/` with a `quarantine_reason` column and `quarantined_at` timestamp. They never block the pipeline. An analyst can inspect them to identify systematic data quality issues.

## Data Model

### Star Schema (DuckDB)

```
fact_flights                     dim_airport
-----------                      -----------
flight_id (PK)                   airport_icao (PK)
callsign                         name
airline_icao (FK)                type
departure_icao (FK)              latitude_deg
arrival_icao (FK)                longitude_deg
scheduled_departure              elevation_ft
scheduled_arrival                iso_country
actual_departure                 municipality
actual_arrival                   iata_code
status                           score
delay_minutes                    scheduled_service
cancelled
source
ingestion_date

dim_airline                      dim_date
-----------                      --------
airline_icao (PK)                date (PK)
airline_name                     year
                                 month
                                 day
                                 day_of_week
                                 quarter
                                 is_weekend
                                 month_name

dim_fuel
--------
date (PK)
region (PK)
price_per_litre
currency
```

### Gold Marts

| Mart | Grain | Key Metrics |
|------|-------|-------------|
| `airport_metrics` | Per airport | total_flights, avg_delay, max_delay, on_time_rate |
| `airline_rankings` | Per airline | total_flights, avg_delay, on_time_rate, rank |
| `delay_analysis` | Per status | flight_count, avg/min/max delay |
| `weather_impact` | Per weather condition | flight_count, avg_delay, avg_temperature, avg_wind |
| `seasonal_trends` | Per date + hour | flight_count, avg_delay |
| `fuel_price_series` | Per date + region | price_per_litre |

### On-Time Performance

The 15-minute threshold for on-time performance aligns with both FAA and EU industry standards. Flights arriving within 15 minutes of schedule are classified as on-time. This threshold is used throughout the Gold marts when computing `on_time_rate`.

## Validation Rules

Validation runs during the Bronze-to-Silver transformation. Failing rows are quarantined, never dropped silently.

| Rule | Source | Implementation |
|------|--------|----------------|
| `flight_id` is not null/empty | flights | `pl.col("flight_id").is_not_null() & (pl.col("flight_id") != "")` |
| `departure_icao != arrival_icao` | flights | Prevents same-airport "flights" |
| `delay_minutes >= 0` | flights | `pl.col("delay_minutes").clip(lower_bound=0)` |
| `temperature_c` between -80 and 60 | weather | `pl.col("temperature_c").is_between(-80.0, 60.0)` |
| `station_icao` is not null | weather | `pl.col("station_icao").is_not_null()` |
| `country`, `date`, `name` not null | holidays | Composite null check |
| `date`, `price_per_litre` not null | fuel | Composite null check |
| Unique per natural key | all | Deduplication on source-specific keys |

## EU Context

The platform is explicitly scoped to European aviation:

- **1,658 airports** across 50 countries, filtered from the global OpenFlights database using ICAO two-letter prefixes (Iceland `BI` through Turkey `LT`)
- **46 ICAO prefix groups** covering EU/EEA, UK, Switzerland, Turkey, Russia, Belarus, Ukraine, Caucasus, and Balkan states
- **EU261/2004 awareness** — delay classification accounts for compensation brackets (EUR 250-600 based on distance/delay), with weather classified as "extraordinary circumstances" under the regulation
- **10 major European hubs** as primary monitoring targets: FRA, LHR, CDG, AMS, MUC, MAD, BCN, FCO, VIE, ZRH
- **10 European carriers** in the synthetic data: Lufthansa, British Airways, Air France, KLM, Iberia, Ryanair, EasyJet, Aer Lingus, Swiss, Austrian Airlines
- **ICAO codes used throughout** — 4-letter airport codes (EDDF, EGLL, LFPG) and 3-letter airline codes (DLH, BAW, AFR) as the primary identifiers, consistent with Eurocontrol operational standards

## Technology Decisions

| Decision | Chosen | Alternatives Considered | Rationale |
|----------|--------|------------------------|-----------|
| OLAP engine | DuckDB | PostgreSQL, Snowflake, BigQuery | Embedded (no server), columnar, reads Parquet natively, portable for reproducible demos |
| Data processing | Polars | Pandas, PySpark | 10-30x faster than Pandas on multi-core, lazy evaluation, Rust core, clean API. PySpark overkill for single-node |
| Data lake | Hugging Face Hub | AWS S3, GCS, local filesystem | Free, versioned datasets, no IAM setup, direct Polars/Pandas download API |
| Orchestration | GitHub Actions | Airflow, Dagster, Prefect | Free, no infrastructure, integrated with repo, sufficient for cron-based batch jobs |
| Data modeling | dbt-core | Custom SQL scripts | Industry standard, built-in testing, documentation generation, lineage |
| API | FastAPI | Flask, Django | Async, auto-generated OpenAPI docs, Pydantic validation |
| BI | Apache Superset + self-contained HTML | Metabase, Grafana | SQL-native, Docker-based, integrates with DuckDB. HTML dashboard for offline demos |
| Language | Python 3.13 | - | Modern typing (3.12+ union syntax), performance improvements, wide ecosystem |

## Configuration

All configuration lives in environment variables (never committed). See `.env.example`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MOCK_MODE` | `false` | Deterministic synthetic data when API keys are unavailable |
| `AVIATIONSTACK_API_KEY` | - | AviationStack fuel prices |
| `OPENWEATHER_API_KEY` | - | Weather conditions per airport |
| `AIRPORTDB_API_TOKEN` | - | Airport metadata enrichment |
| `OPENSKY_USERNAME` / `OPENSKY_PASSWORD` | - | OpenSky live flight data |
| `HF_TOKEN` / `HF_REPO` | - | Hugging Face dataset upload |
| `DELAY_THRESHOLD_MINUTES` | `15` | On-time performance threshold |
| `REQUEST_TIMEOUT_SECONDS` | `15` | HTTP client timeout |
| `MAX_RETRIES` | `3` | Retry count with exponential backoff |
| `RATE_LIMIT_DELAY_SECONDS` | `0.25` | Inter-request delay for rate-limited APIs |

## Quick Start

### Local (Python)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
make setup
cp .env.example .env
make run        # full pipeline (mock mode)
make api        # start FastAPI on :8000
make streamlit  # interactive dashboard on :8501
```

### Docker Compose

```bash
cp .env.example .env
make docker-up  # API + Superset + auto-bootstrap
```

- API: http://localhost:8000/docs
- Superset: http://localhost:8088 (admin / admin)

The `superset-init` container creates the DuckDB connection and an "Air Traffic Overview" dashboard with 4 charts on first boot.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe, storage readiness, credential status |
| GET | `/sources` | Registered data sources and collector classes |
| POST | `/ingest/{source}` | Run a single collector |
| POST | `/ingest` | Run all collectors |
| POST | `/pipeline/run` | Full ETL pipeline (collect -> bronze -> silver -> gold -> warehouse -> quality) |
| GET | `/pipeline/report` | Last pipeline run report (step timings, success/failure) |
| GET | `/quality/report` | Data-quality report (pass-rates, quarantine counts, freshness) |
| POST | `/quality/check` | Run data-quality scan on demand |
| GET | `/warehouse/tables` | DuckDB table inventory with row counts |
| POST | `/warehouse/query` | Read-only SQL query against the warehouse |
| GET | `/kpis` | Headline business KPIs from Gold layer |
| GET | `/dashboard` | Self-contained HTML analytics dashboard (offline, no CDN) |
| POST | `/dashboard/refresh` | Regenerate dashboard from current warehouse |

## Streamlit Dashboard

Run `make streamlit` to launch an interactive dashboard on `http://localhost:8501` with:

- **Overview** — KPI cards, flight status pie chart, delay distribution
- **Airports** — volume vs delay scatter, top airports bar chart
- **Airlines** — on-time performance ranking, delay comparison
- **Weather** — delay by condition, temperature vs delay correlation
- **Delays** — status breakdown, daily trend with dual-axis (volume + delay)
- **Quality** — pass-rates per source, quarantine file inventory
- **SQL Console** — ad-hoc queries against the DuckDB warehouse with CSV export
- **Pipeline** — trigger runs, view reports, system status

## Data Quality Framework

The quality module (`pipelines/quality.py`) computes per-source metrics after each pipeline run:

```
Source       Bronze    Silver    Quarantined    Pass Rate
-----------  --------  --------  -------------  ----------
airports     19,896    1,658     0              100.0%
flights      2,000     389       565            40.7%
weather      3,200     118       24             83.1%
holidays     5,000     673       0              100.0%
fuel         3,200     7,215     0              100.0%
```

Results are written to `warehouse/checkpoints/quality_report.json` and exposed via `GET /quality/report`.

## Testing

```bash
make verify     # ruff check + ruff format + mypy + pytest (with coverage gate)
```

- **Unit tests** — Silver transforms, Gold mart logic, quality framework, dashboard generation, HF upload planning
- **Integration tests** — Full pipeline against isolated temp warehouse (idempotency verified), FastAPI endpoint tests
- **Coverage gate** — 50% combined coverage enforced in CI
- **Type checking** — mypy across config, ingestion, pipelines, apps, scripts

## CI/CD

**CI** (`.github/workflows/ci.yml`) — runs on PR and push to main:
1. Lint (ruff check) + format (ruff format --check)
2. Type check (mypy)
3. Tests with coverage gate (pytest --cov-fail-under=50)
4. End-to-end pipeline + dbt build + dbt docs generate

**ETL** (`.github/workflows/etl.yml`) — scheduled every 6 hours:
1. Full pipeline run (mock or live depending on secrets)
2. dbt model build + tests
3. Dashboard HTML generation
4. Artifact upload (pipeline report, quality report, warehouse, dashboard)
5. Failure notification via GitHub issue

Concurrency guard prevents parallel DuckDB writes (single-writer database).

## Repository Structure

```
.
  config/                    pydantic-settings config + logging
  ingestion/
    airports/                OpenFlights + AirportDB enrichment + EU filter
    flights/                 OpenSky flight states
    weather/                 OpenWeather conditions
    holidays/                Holiday calendar
    fuel/                    AviationStack fuel prices
    base.py                  Collector ABC + CheckpointStore watermarks
    registry.py              Source -> Collector registry
    synthetic.py             Deterministic mock data (EU airports/airlines)
    utils.py                 Retry, atomic_write_json, rate limiting
  pipelines/
    bronze.py                JSONL -> Parquet (1:1, idempotent)
    silver.py                Clean/validate/deduplicate -> quarantine DLQ
    gold.py                  6 analytical marts (Polars)
    warehouse.py             DuckDB star schema + gold views
    quality.py               Data-quality pass-rate reporting
    orchestrator.py          Sequential DAG + PipelineReport
  apps/main.py               FastAPI ingestion + analytics API
  scripts/
    build_dashboard.py       Offline HTML dashboard generator
    upload_hf.py             Hugging Face dataset upload
  dbt/                       dbt-duckdb project (6 models, 15 tests)
  docker/                    Dockerfile, compose, Superset init
  tests/                     unit + integration tests
  warehouse/                 Generated Medallion layers (gitignored)
```

## Sample Queries

Top 5 airlines by on-time performance:

```sql
SELECT airline_icao, airline_name, total_flights, on_time_rate
FROM gold_airline_rankings
ORDER BY on_time_rate DESC
LIMIT 5;
```

Weather impact on delays:

```sql
SELECT weather_condition, flight_count, avg_delay_minutes
FROM gold_weather_impact
ORDER BY flight_count DESC;
```

Daily traffic trend (last 14 days):

```sql
SELECT flight_date, SUM(flight_count) AS daily_flights
FROM gold_seasonal_trends
GROUP BY flight_date
ORDER BY flight_date DESC
LIMIT 14;
```

Via API:

```bash
curl -s localhost:8000/warehouse/query \
  -H 'Content-Type: application/json' \
  -d '{"sql": "SELECT airline_icao, total_flights, avg_delay_minutes FROM gold_airline_rankings ORDER BY avg_delay_minutes LIMIT 5"}'
```

## Documentation

A comprehensive documentation vault lives in `Air Traffic Warehouse/` — 56 Obsidian-compatible documents covering domain knowledge (aviation, airports, delays, weather), data engineering concepts (Medallion, star schema, SCD, partitioning), technology deep-dives (DuckDB, Polars, dbt), and architecture decision records.

## License

This is a portfolio project demonstrating data engineering capabilities. The code is provided as-is for educational and demonstration purposes.
