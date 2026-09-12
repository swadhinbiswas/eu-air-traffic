#                      ✈️ Air Traffic Analytics Platform

**A production-style batch data platform for European aviation analytics** — flight delays, weather correlations, airline benchmarking, and operational KPIs. Built on a Medallion architecture with Polars, DuckDB, dbt, FastAPI, and GitHub Actions.

> Zero infrastructure cost. Fully reproducible. Runs entirely on free-tier services and GitHub Actions runners.

![Python](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)
![Polars](https://img.shields.io/badge/polars-data%20engine-CD792C)
![DuckDB](https://img.shields.io/badge/duckdb-OLAP-FFF000)
![dbt](https://img.shields.io/badge/dbt-core-FF694B?logo=dbt&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async%20API-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)
![CI](https://img.shields.io/github/actions/workflow/status/swadhinbiswas/air-traffic/ci.yml?label=CI)

---

## Table of Contents

- [Overview](#overview)
- [Key Highlights](#key-highlights)
- [Architecture](#architecture)
- [Data Sources](#data-sources)
- [Medallion Layers](#medallion-layers)
- [Data Model](#data-model)
- [Gold Marts](#gold-marts)
- [Validation Rules](#validation-rules)
- [EU Regulatory Context](#eu-regulatory-context)
- [Technology Decisions](#technology-decisions)
- [Configuration](#configuration)
- [Quick Start](#quick-start)
- [API Endpoints](#api-endpoints)
- [Streamlit Dashboard](#streamlit-dashboard)
- [Data Quality Framework](#data-quality-framework)
- [Testing](#testing)
- [CI/CD](#cicd)
- [Repository Structure](#repository-structure)
- [Sample Queries](#sample-queries)
- [Documentation](#documentation)
- [License](#license)
- [Contact](#contact)

---

## Overview

European aviation generates massive volumes of operational data — flights, weather, fuel prices, schedules — but analysing delay patterns, weather impact, and airline performance requires joining data across multiple sources with different schemas, formats, and quality levels.

This platform ingests raw data from public APIs, validates and cleans it through a layered pipeline, and produces analytics-ready marts that answer concrete business questions:

- Which airports have the worst on-time performance?
- How does weather correlate with delay severity?
- What is the estimated cost impact of delays under EU261/2004?
- How do seasonal patterns affect flight volume and delays?

## Key Highlights

A quick summary of what this project demonstrates, for anyone scanning the repo:

- **Medallion architecture done properly** — Bronze (raw), Silver (validated/deduplicated), Gold (business-ready marts), plus a dead-letter Quarantine layer and checkpoint watermarks for idempotent, resumable runs.
- **Modern, fast tooling** — Polars for 10–30x faster-than-Pandas processing, DuckDB as an embedded columnar OLAP engine, dbt-core for modelled, tested, documented transformations.
- **Real domain knowledge** — EU261/2004 compensation logic, ICAO airport/airline coding, 46 ICAO prefix groups spanning 50 European and neighbouring countries.
- **A real 24/7 live edge** — one VPS collector polls adsb.lol, OpenSky, aviationweather and Open-Meteo, publishes eight Kafka topics, and serves every live layer (planes, routes, speed, weather, fuel, CO₂) from a single `GET /live/snapshot`.
- **Operational airspace intelligence** — every aircraft is classified as cargo, military, passenger, business, private, helicopter, glider, drone, balloon or ground. Classification is data-driven: **ICAO DOC 8643** type designators (~2,800 types) and the **OpenFlights** operator database (~5,800 airlines) plus the ADS-B emitter category. Each aircraft also carries an estimated CO₂/fuel rate, so the map and analytics show the whole European picture, not just airline traffic.
- **Fully automated lake + warehouse** — GitHub Actions drains Kafka → Bronze/Silver Parquet on Hugging Face, then runs dbt → Gold on MotherDuck, on a schedule, and opens an issue automatically on failure.
- **Quality-first engineering** — typed with mypy, linted/formatted with ruff, tested with pytest under a coverage gate, with a dedicated data-quality framework tracking pass rates and quarantine counts per source.
- **Multiple consumption surfaces** — an interactive Streamlit dashboard, Apache Superset, a self-contained offline HTML dashboard, and the React "God's Eye View" app.
- **Zero-cost by design** — the VPS runs the collector; Aiven Kafka, Hugging Face Hub and MotherDuck free tiers, plus GitHub Actions, keep the whole platform running without paid infrastructure while remaining reproducible via Docker Compose.

## Architecture

![Platform Architecture](images/1.png)

**Two planes.** *Live edge:* VPS collector (only always-on process) → Kafka + `GET /live/snapshot`. *Lake:* GitHub Actions every 9 min drains Kafka → Bronze → incremental Silver on Hugging Face. *Warehouse:* Silver → dbt → Gold on MotherDuck. Batch pipeline: `collect → bronze → silver → warehouse → gold (dbt) → quality → HF upload` (orchestrator order, local/CI).

## Live Dashboard — God's Eye View

A production-grade React dashboard (`web/`) inspired by [gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view): a **MapLibre GL globe** (via [mapcn](https://mapcn.dev)) rendering real dark Earth tiles with live ADS-B aircraft as rotated plane silhouettes, clickable airports, route arcs and an Open-Meteo weather layer — plus a full multi-page analytics surface.

| Page | What it shows |
|------|----------------|
| **Overview** | God's Eye View — live MapLibre globe: real-time aircraft telemetry (rotated silhouettes, smooth dead-reckoning, emergency highlight), airports, route arcs, METAR flight-category layer, RainViewer radar, day/night terminator, HUD KPIs, search and click-for-details inspector |
| **Analytics** | Gold-layer marts with a mapcn route-network map + traffic/delay/seasonal charts, **live airspace** analysis (altitude distribution, common types, airlines-from-callsigns, emergencies) and live aviation-weather analytics |
| **Stories** | Live "right now" insights (busiest operator, common airframe, live altitude mix) plus batch narratives auto-derived from the Gold layer |
| **Catalog** | Tables, schemas, columns, row counts and the dbt lineage graph (parsed from the model DAG) |
| **Explorer** | Dataset browser with column metadata, sample records and CSV export |
| **SQL** | Query workbench — uses the native **DuckDB** backend when the API is reachable, otherwise falls back to in-browser **DuckDB-WASM** over the bundled Gold layer |
| **Ops** | Pipeline steps, data-quality pass rates per source, quarantine counts and live stream health |
| **Docs** | Medallion architecture, model catalogue, data sources and business glossary |

**Hybrid data model.** The dashboard fetches its data at runtime — Gold analytics from **Turso** (libSQL) in the browser using a **read-only token**, live aircraft/weather from the VPS snapshot API. There is no build-time data bundle and no warehouse API in the read path. MotherDuck remains the dbt warehouse; GitHub Actions publishes a derived serving copy to Turso (small tables replaced, the two growing fact tables synced incrementally against a watermark).

```bash
make web-data     # regenerate the static bundle from the DuckDB warehouse
make web-install  # install frontend dependencies
make web          # run the dashboard in dev mode (Vite, http://localhost:5173)
make web-build    # type-check + production build → web/dist
```

The bundle is produced by `scripts/build_web_bundle.py`, which reads the batch warehouse (local DuckDB built from the Silver layer), the EU airport reference file and the dbt model graph.

### Live data API (VPS collector)

The dashboard needs live aircraft, weather, fuel and emissions, but community ADS-B APIs block browser CORS (and Cloudflare egress). The **VPS collector owns the edge**: one long-lived process polls every upstream, publishes to Kafka, and serves a single CORS-enabled snapshot API.

```
VPS collector (24/7, systemd) — the only always-on process
  ├─ adsb.lol / OpenSky  → positions ─┐  (live store every 15s)
  ├─ OpenSky             → flights    │  (Kafka every 5 min for positions)
  ├─ aviationweather     → METAR/TAF  ├─▶ Kafka (Aiven, 24h retention)
  ├─ Open-Meteo          → forecast   │       │
  ├─ AviationStack       → fuel       └─▶ LiveStore → GET /live/snapshot → dashboard
  └─ reference           → airports / routes / fleet / emission factors
                                              │
GitHub Actions every 9 min ───────────────────┘
  sink (offset resume) → Bronze → incremental Silver → HF
  → DuckDB star schema → dbt Gold → MotherDuck
```

`GET /live/snapshot` returns everything in one payload: positions (with a live CO₂ estimate and operational class), flight movements, METAR/TAF/forecast, fuel and reference data. Build the dashboard with `VITE_LIVE_URL=https://live.example.com`; expose the VPS with Cloudflare Tunnel or Caddy.

**Cadence:** the live map updates every **15s**, but positions are only published to Kafka every **5 minutes** — persisting every tick would be ~17M rows/day and is not needed for analytics. Every other source publishes each cycle. The Actions sink resumes from its committed Kafka offset, so each 9-minute run drains exactly the records accumulated since the prior run; Kafka retention must exceed the schedule gap (the collector pins it to 24h).

Live aircraft are rendered as **real rotated plane silhouettes**, dead-reckoned between polls using ground speed and track so they glide smoothly. Clicking any aircraft shows full telemetry — Mach, IAS/TAS, OAT, wind aloft, squawk, vertical rate — plus registration, type and operator. Emergencies (squawk 7500/7600/7700) are highlighted in red.

## Data Sources

All sourced by the VPS collector (`services/collector.py`), normalised, published to per-product Kafka topics, and persisted to the lake.

| Source | What | Coverage | Auth |
|--------|------|----------|------|
| adsb.lol | Live ADS-B telemetry (alt, gs, Mach, OAT, wind, squawk, reg, type) | EU coverage circles | None |
| OpenSky Network | Flight movements; positions fallback | Global (EU focus) | Optional |
| aviationweather.gov | METAR (flight category) + TAF | Europe bbox | None |
| Open-Meteo | Current + 24h hourly forecast per airport | Global | None |
| AviationStack | Fuel prices | Global | API key |
| OpenFlights / OurAirports | Airport, route and fleet reference (EU-filtered to 1,658) | Global | Static CSV |
| ICAO methodology | Aircraft emission factors (CO₂ per type) | — | Derived |

## Medallion Layers

| Layer | Purpose |
|-------|---------|
| **Bronze** | Raw data landed as-is from source APIs. Appended per run, never modified. Stored as JSONL (ingestion) and consolidated Parquet (processing). Checkpoint watermarks prevent re-fetching already-collected windows. |
| **Silver** | Cleansed, validated, deduplicated. Timestamps normalised to UTC. Bad rows quarantined with reason and timestamp. Natural keys used for idempotent deduplication (e.g. `flight_id`, `station_icao + timestamp`). **Incremental**: only Bronze files written since the last successful run are processed (watermark in `checkpoints/silver_watermarks.json`), so a 9-minute cadence stays flat-cost. `python -m pipelines.silver --full` reprocesses everything. |
| **Gold** | Business-ready analytical marts modelled in **dbt** (staging → intermediate → marts → reports), tested and documented. `dbt build` materialises the 11 marts as views in the `marts` schema; `pipelines.warehouse --register-gold` exposes them as `gold_*` views in `main` and the warehouse is published to **MotherDuck**. |
| **Quarantine** | Dead-letter queue. Rows failing Silver validation are written to `warehouse/quarantine/<source>/` with a `quarantine_reason` column and `quarantined_at` timestamp. They never block the pipeline — an analyst can inspect them to identify systematic data quality issues. |

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

### On-Time Performance

Flights arriving within **15 minutes** of schedule are classified as on-time — aligned with both FAA and EU industry standards. This threshold (`DELAY_THRESHOLD_MINUTES`) is used throughout the Gold marts when computing `on_time_rate`.

## Gold Marts

Built by **dbt** (`make dbt` / `dbt build`), which also runs the schema tests and writes lineage + docs to `dbt/target/`. Every dbt `marts` model is exposed as a `gold_*` view in `main` by `pipelines/warehouse.py --register-gold`, then published to **MotherDuck** by the `Warehouse` workflow.

| Mart | Grain | Key Metrics |
|------|-------|-------------|
| `airport_metrics` | Per airport | total_flights, avg_delay, max_delay, on_time_rate |
| `airline_rankings` | Per airline | total_flights, avg_delay, on_time_rate, rank |
| `delay_analysis` | Per status | flight_count, avg/min/max delay |
| `weather_impact` | Per weather condition | flight_count, avg_delay, avg_temperature, avg_wind |
| `seasonal_trends` | Per date + hour | flight_count, avg_delay |
| `fuel_price_series` | Per date + region | price_per_litre |
| `route_performance` | Per route | total_flights, avg_delay, on_time_rate, avg_distance_km |
| `aircraft_utilization` | Per aircraft type | capacity, range_km, routes_served, co2_kg_per_hour |
| `emissions_analysis` | Per aircraft type | fuel_burn, co2_kg_per_hour, emission_factor |
| `notam_summary` | Per location + type | notam_count, latest_notam |
| `sector_analysis` | Per departure airport + date | flight_count, avg_delay, on_time_rate |
| `aircraft_class_mix` | Per operational class | aircraft, avg_altitude_ft, avg_ground_speed_kt, total_co2_kg_per_hour |

Plus two report models: `data_freshness` and `quality_trends`.

```bash
make dbt          # dbt build (models + tests) against the warehouse
make dbt-test     # tests only
make dbt-docs     # generate + serve the lineage/documentation site
```

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

## EU Regulatory Context

The platform is explicitly scoped to European aviation:

- **1,658 airports** across 50 countries, filtered from the global OpenFlights database using ICAO two-letter prefixes (Iceland `BI` through Turkey `LT`)
- **46 ICAO prefix groups** covering EU/EEA, UK, Switzerland, Turkey, Russia, Belarus, Ukraine, Caucasus, and Balkan states
- **EU261/2004 awareness** — delay classification accounts for compensation brackets (EUR 250–600 based on distance/delay), with weather classified as "extraordinary circumstances" under the regulation
- **10 major European hubs** as primary monitoring targets: FRA, LHR, CDG, AMS, MUC, MAD, BCN, FCO, VIE, ZRH
- **10 European carriers** in the synthetic data: Lufthansa, British Airways, Air France, KLM, Iberia, Ryanair, EasyJet, Aer Lingus, Swiss, Austrian Airlines
- **ICAO codes used throughout** — 4-letter airport codes (EDDF, EGLL, LFPG) and 3-letter airline codes (DLH, BAW, AFR) as primary identifiers, consistent with Eurocontrol operational standards

## Technology Decisions

| Decision | Chosen | Alternatives Considered | Rationale |
|----------|--------|--------------------------|-----------|
| Live edge | VPS collector (systemd) | Cloudflare Worker, GitHub Actions cron | Community ADS-B APIs block Cloudflare egress; an always-on node gives gap-free 15s positions and one CORS endpoint |
| Event bus | Aiven Kafka (REST + native) | SQS, Pub/Sub, Redis Streams | Durable replayable log, free tier, decouples the 24/7 collector from the lake writer |
| OLAP engine | DuckDB | PostgreSQL, Snowflake, BigQuery | Embedded (no server), columnar, reads Parquet natively, portable for reproducible demos |
| Data processing | Polars | Pandas, PySpark | 10–30x faster than Pandas on multi-core, lazy evaluation, Rust core, clean API. PySpark overkill for single-node |
| Data lake | Hugging Face Hub | AWS S3, GCS, local filesystem | Free, versioned datasets, no IAM setup, direct Polars/Pandas download API |
| Orchestration | GitHub Actions | Airflow, Dagster, Prefect | Free, no infrastructure, integrated with repo, sufficient for cron-based batch jobs |
| Data modelling | dbt-core | Custom SQL scripts | Industry standard, built-in testing, documentation generation, lineage |
| Serving layer | MotherDuck | Local DuckDB, Postgres | Serverless DuckDB the dashboard/BI can query without shipping a database file |
| API | FastAPI | Flask, Django | Async, auto-generated OpenAPI docs, Pydantic validation |
| BI | Apache Superset + self-contained HTML | Metabase, Grafana | SQL-native, Docker-based, integrates with DuckDB. HTML dashboard for offline demos |
| Language | Python 3.13 | — | Modern typing (3.12+ union syntax), performance improvements, wide ecosystem |

## Configuration

All configuration lives in environment variables (never committed). See `.env.example`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MOCK_MODE` | `false` | Deterministic synthetic data when API keys are unavailable |
| `AVIATIONSTACK_API_KEY` | – | AviationStack fuel prices |
| `OPENSKY_USERNAME` / `OPENSKY_PASSWORD` | – | OpenSky flights + positions fallback |
| `AIVEN_KAFKA_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` | – | Aiven Kafka broker (the event bus) |
| `KAFKA_TOPIC_*` | `eu-positions` … | 5 topics: positions, flights, weather (metar+taf+forecast), fuel, reference |
| `LIVE_API_HOST` / `LIVE_API_PORT` | `0.0.0.0` / `8090` | Live snapshot API bind address |
| `LIVE_API_PUBLIC_URL` | – | Public URL the dashboard is built against |
| `HF_TOKEN` / `HF_REPO` | – | Hugging Face dataset (Bronze/Silver lake) |
| `HF_BRONZE_PREFIX` / `HF_SILVER_PREFIX` | `bronze` / `silver` | Lake path prefixes |
| `MOTHERDUCK_TOKEN` / `MOTHERDUCK_DATABASE` | – / `air_traffic` | Gold warehouse (dbt target) |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | – | Turso serving copy (written by Actions) |
| `VITE_TURSO_URL` / `VITE_TURSO_TOKEN` | – | Site read path; token must be **read-only** |
| `MOTHERDUCK_PG_URL` | – | Postgres-wire endpoint for BI/psql (same token as password) |
| `WAREHOUSE_TARGET` | `local` | Build target: `local` (reproducible) or `motherduck` |
| `DBT_TARGET` | `dev` | `dev` (local DuckDB) or `motherduck` |
| `DELAY_THRESHOLD_MINUTES` | `15` | On-time performance threshold |
| `VITE_LIVE_URL` | – | VPS live API base URL (build-time) |
| `VITE_API_URL` | `http://localhost:8000` | FastAPI backend URL for native SQL (build-time) |

See `.env.example` for the complete, commented list (collector cadence, sink batching, storage paths).

## Quick Start

### Local (Python)

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/).

```bash
make setup
cp .env.example .env
make run              # batch Medal​lion pipeline (mock mode) → local DuckDB
make collector-once   # one poll of every VPS source → Kafka (or Bronze if unset)
make collector        # 24/7 collector + live API on :8090
make sink             # drain Kafka → Bronze Parquet → Hugging Face
make api              # FastAPI (warehouse) on :8000
make streamlit        # interactive dashboard on :8501
make web-data && make web   # God's Eye View dashboard on :5173
```

Then point the dashboard at the live API: `VITE_LIVE_URL=http://localhost:8090 make web-build`.

### Deploy the collector (VPS)

```bash
sudo useradd -r -s /usr/sbin/nologin airtraffic
sudo mkdir -p /opt/eu-air-traffic && sudo chown airtraffic: /opt/eu-air-traffic
# copy the repo + a filled .env, create the venv, then:
sudo cp deploy/eu-collector.service /etc/systemd/system/
sudo systemctl enable --now eu-collector
journalctl -u eu-collector -f
```

The collector is the **only** process that runs on the VPS. It is I/O-bound (idle CPU), so a 2-core box is plenty; it uses 4 concurrent fetches per source and shuts down cleanly on `SIGTERM` (systemd `TimeoutStopSec=30`). Kafka → Bronze → Silver → Gold all run ephemerally in GitHub Actions.

### HTTPS for the live API (Caddy)

The dashboard is served over HTTPS, and browsers block an HTTPS page from calling
an `http://` API (mixed content). Put Caddy in front of the collector — it
terminates TLS with an automatic Let's Encrypt certificate:

```bash
# 1. DNS: point a hostname at the VPS (A record), e.g. vps.example.com -> <vps-ip>

# 2. Install Caddy
sudo apt install caddy      # Debian/Ubuntu
sudo pacman -S caddy        # Arch

# 3. Install the site config and start it
sudo cp /opt/eu-air-traffic/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
systemctl reload caddy

# 4. Firewall: allow the ACME challenge + TLS
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp

# 5. Verify (certificate is issued on first request)
curl -s https://vps.example.com/health
```

Then tighten the collector so it is only reachable through Caddy — set
`LIVE_API_HOST=127.0.0.1` in `/opt/eu-air-traffic/.env`, restart it, and close
the raw port:

```bash
sudo systemctl restart eu-collector
sudo ufw delete allow 8090/tcp   # if it was opened manually
```

Finally point the site at it and rebuild:

```bash
VITE_LIVE_URL=https://vps.example.com npm run build
```

`VITE_*` values are baked in at **build time** — changing `.env` after a build
has no effect until you rebuild. In GitHub Actions, set the repository variable
`VITE_LIVE_URL` (Settings → Secrets and variables → Actions → Variables) and
re-run the `Frontend` workflow.

### Docker Compose

```bash
cp .env.example .env
make docker-up  # API + Superset + auto-bootstrap
```

- API docs: http://localhost:8000/docs
- Superset: http://localhost:8088 (`admin` / `admin`)

The `superset-init` container creates the DuckDB connection and an "Air Traffic Overview" dashboard with 4 charts on first boot.

## API Endpoints

### Live API (VPS collector, port 8090)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Collector liveness + per-section freshness |
| GET | `/live/snapshot` | **The one call the dashboard needs** — positions, flights, weather, fuel, reference, emissions |
| GET | `/live/positions` | Live aircraft (optional `limit`) |
| GET | `/live/flights` | Recent movements |
| GET | `/live/weather` | METAR + TAF + Open-Meteo forecast |
| GET | `/live/fuel` | Jet-fuel price series |
| GET | `/live/emissions` | Live CO₂ rate by aircraft type |
| GET | `/live/taf?ids=EDDF,EGLL` | TAF for specific stations |
| GET | `/live/aircraft/{hex}` | Registration/type enrichment for one airframe |
| GET | `/live/reference/{kind}` | airports, routes, aircraft, emission_factors, holidays |

### Warehouse API (FastAPI, port 8000)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe, storage readiness, credential status |
| GET | `/sources` | Registered data sources and collector classes |
| POST | `/ingest/{source}` | Run a single collector |
| POST | `/ingest` | Run all collectors |
| POST | `/pipeline/run` | Full ETL pipeline (collect → bronze → silver → warehouse → gold → quality) |
| GET | `/pipeline/report` | Last pipeline run report (step timings, success/failure) |
| GET | `/quality/report` | Data-quality report (pass rates, quarantine counts, freshness) |
| POST | `/quality/check` | Run data-quality scan on demand |
| GET | `/warehouse/tables` | DuckDB/MotherDuck table inventory with row counts |
| POST | `/warehouse/query` | Read-only SQL query against the warehouse |
| GET | `/kpis` | Headline business KPIs from the Gold layer |
| GET | `/dashboard` | Self-contained HTML analytics dashboard (offline, no CDN) |
| POST | `/dashboard/refresh` | Regenerate dashboard from current warehouse |

## Streamlit Dashboard

Run `make streamlit` to launch an interactive dashboard on `http://localhost:8501` with:

- **Overview** — KPI cards, flight status pie chart, delay distribution
- **Airports** — volume vs delay scatter, top airports bar chart
- **Airlines** — on-time performance ranking, delay comparison
- **Weather** — delay by condition, temperature vs delay correlation
- **Delays** — status breakdown, daily trend with dual-axis (volume + delay)
- **Quality** — pass rates per source, quarantine file inventory
- **SQL Console** — ad-hoc queries against the DuckDB warehouse with CSV export
- **Pipeline** — trigger runs, view reports, system status

> Add a screenshot or GIF of the dashboard here — a visual is often the fastest way for a recruiter to grasp the project.

### Streamlit Community Cloud Deployment

The dashboard is fully ready to be deployed to [Streamlit Community Cloud](https://share.streamlit.io/). 

Because the app is configured to gracefully fallback to reading from your public **Hugging Face Hub** repository if a local DuckDB file is missing, you can deploy it instantly:
1. Push this repository to GitHub.
2. Connect the repository in Streamlit Community Cloud.
3. Set the **Main file path** to `streamlit_app.py`.
4. Deploy! The app will automatically read your `requirements.txt`, install dependencies, fetch `air_traffic.duckdb` from Hugging Face, and serve your analytics.

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
- **Integration tests** — full pipeline against an isolated temp warehouse (idempotency verified), FastAPI endpoint tests
- **Coverage gate** — 50% combined coverage enforced in CI
- **Type checking** — mypy across config, ingestion, pipelines, apps, scripts

## CI/CD

**CI** (`.github/workflows/ci.yml`) — runs on PR and push to `main`:
1. Lint (`ruff check`) + format (`ruff format --check`)
2. Type check (`mypy`, including `services`)
3. Tests with coverage gate (`pytest --cov-fail-under=50`)
4. End-to-end mock pipeline + dbt build + docs generate, plus the React build

**Lake** (`.github/workflows/lake.yml`) — every 9 minutes:
1. Drain the Kafka backlog since the last committed offset (≈9-minute window)
2. Bronze Parquet → incremental Silver transform
3. Push Silver to the Hugging Face dataset
4. Build the star schema, `dbt build`, register `gold_*`, publish to MotherDuck
5. Publish the site tables + serving copy to Turso (read-only token for the web)

**Bundle** (`.github/workflows/bundle.yml`) — hourly:
1. Build the offline HTML dashboard and the static JSON bundle from Silver
2. Build the React dashboard and upload the artifacts

A concurrency guard prevents parallel DuckDB/MotherDuck writes. Only the collector runs on the VPS; all batch/transform work is ephemeral GitHub Actions.

## Repository Structure

```
.
├── Air Traffic Warehouse/            Obsidian-compatible documentation vault
├── config/                   pydantic-settings config + logging
├── services/                  ── LIVE EDGE (runs on the VPS) ──────────────
│   ├── collector.py            24/7 process: poll → Kafka + live API
│   ├── live_api.py               FastAPI app serving GET /live/snapshot
│   ├── live_store.py              Thread-safe latest-data store
│   ├── sink.py                     Kafka → Bronze Parquet → Hugging Face
│   ├── sources/                    positions, flights, weather, forecast, fuel, reference
│   ├── emissions.py                 ICAO CO₂ estimates + live fleet summary
│   ├── classification.py             ICAO 8643 + OpenFlights aircraft classifier
│   ├── enrichment.py                 One place to attach class + CO₂ to a position
│   ├── data/                        Generated reference data (aircraft types, airlines)
│   ├── hf_lake.py                   Hugging Face dataset helpers
│   ├── kafka_bus.py                 Aiven Kafka producer
│   └── bronze.py                    Raw JSONL writer
├── ingestion/                 Batch collectors (registry, base, synthetic, per-source)
│   ├── reference.py             Local-first access to committed reference data
├── pipelines/
│   ├── bronze.py               JSONL → Parquet (1:1, idempotent)
│   ├── silver.py                 Clean/validate/deduplicate → quarantine DLQ
│   ├── warehouse.py                 DuckDB star schema + gold_* view registration
│   ├── gold.py                       Portable Polars Gold Parquet export
│   ├── quality.py                     Data-quality pass-rate reporting
│   └── orchestrator.py                  Sequential DAG + PipelineReport
├── apps/main.py                FastAPI warehouse + analytics API
├── web/                          React + MapLibre "God's Eye View" dashboard
│   ├── public/data/               Static data bundle (generated)
│   └── src/                        pages, map layers, hooks, DuckDB-WASM SQL engine
├── scripts/
│   ├── build_dashboard.py       Offline HTML dashboard generator
│   ├── build_web_bundle.py        Static JSON bundle for the React dashboard
│   ├── lake_sync.py                Pull/push Bronze & Silver with Hugging Face
│   ├── build_reference_data.py      Regenerate ICAO 8643 + OpenFlights reference data
│   └── publish_motherduck.py        Publish the warehouse to MotherDuck
├── dbt/                          dbt-duckdb project (staging → intermediate → marts → reports)
├── deploy/                        systemd unit for the VPS collector
├── docker/                        Dockerfile, compose, Superset init
├── tests/                          unit + integration tests
└── warehouse/                       Generated Medallion layers (gitignored)
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

A comprehensive documentation vault lives in `Air Traffic Warehouse/` — 56 Obsidian-compatible documents covering domain knowledge (aviation, airports, delays, weather), data engineering concepts (Medallion architecture, star schema, SCD, partitioning), technology deep-dives (DuckDB, Polars, dbt), and architecture decision records.

## License

This is a portfolio project demonstrating data engineering capabilities. The code is provided as-is for educational and demonstration purposes.


## Contact

**Swadhin Biswas**
📍 Dhaka, Bangladesh · 💻 [GitHub](https://github.com/swadhinbiswas) · ✉️ [swadhinbiswas.cse@gmail.com](mailto:swadhinbiswas.cse@gmail.com)

Open to Data Engineering / Analytics Engineering roles across the EU. Feel free to reach out.
