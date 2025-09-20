# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Turso serving layer now serves dashboard KPIs, freshness, counts and the
  catalog's row counts from a precomputed one-row `site_summary` table instead
  of having the browser COUNT/AVG/MAX the fact tables on every poll. Analytics
  route and status aggregations read the existing Gold marts rather than
  re-grouping `fact_flights`.
- The Turso publisher no longer copies `fact_positions` (the live map reads the
  VPS snapshot) or `fact_notams` (served through `gold_notam_summary`), and
  drops both from existing serving databases. Large or slow tables keep a
  refresh floor so a rebuilt warehouse cannot rewrite them every cycle.
- Dashboard polling pauses on hidden tabs and follows the 15-minute lake
  cadence instead of refreshing every 30-60 seconds; the data catalog loads
  independently of analytics.

## [0.1.0] - 2026-09-16

### Added

- Collector service for the VPS: live positions (adsb.lol first, airplanes.live
  and OpenSky as fallbacks), OpenSky flight movements, AirLabs schedules,
  METAR/TAF, Open-Meteo forecasts, jet-fuel prices, and reference data for
  airports, routes, aircraft, emissions and holidays.
- Live snapshot API (FastAPI) served behind Caddy with TLS, gzip and a
  loopback-only bind.
- Kafka event bus with five topics (`eu-positions`, `eu-flights`, `eu-weather`,
  `eu-fuel`, `eu-reference`), keyed records, and 24-hour topic retention.
- Bronze Parquet sink, incremental Silver transforms, a DuckDB star schema and
  a dbt project with 103 models and tests.
- Publishing paths: Hugging Face dataset lake (Bronze and Silver), MotherDuck
  warehouse, Turso serving copy, plus site payloads (stories, ops, lineage) and
  a data-quality report.
- Monthly Eurostat airport benchmark (`avia_paoa`) fetched by GitHub Actions.
- Fuel and CO2 estimator built on a precomputed 37-type OpenAP grid.
- React and Vite dashboard: live map with a real search, aircraft ticker,
  analytics, catalog with search and layer filters, SQL workbench and Ops page.
- Docker Compose deployment (`./deploy.sh`) and a lake job image, alongside the
  systemd and Caddy deployment for the collector.
- VPS dispatch timer so the lake keeps its cadence when GitHub's scheduler is
  late or drops a run.
- Dataset card generator for the Hugging Face lake, project docs, and a JOSS
  manuscript draft.

### Changed

- Flight polling now uses one `/flights/all` request per cycle plus departures
  for a small hub list and a nightly arrivals backfill, replacing per-airport
  loops that exhausted the OpenSky credit budget.
- AirLabs schedule queries try ICAO first and fall back to IATA.
- The lake runs every 15 minutes, and frontend-only pushes no longer queue a
  drain behind the single-writer lock.
- Unchanged Hugging Face uploads are treated as no-ops instead of failures.
- README, environment template and configuration defaults rewritten around the
  current architecture.

### Fixed

- Silent success paths: a Hugging Face upload rejected by a trailing space in
  the configured repo id, and publishers that returned zero without writing.
  Failures now fail the run, and credentials that are set but empty raise.
- Publish cadence that compared `monotonic()` against `0.0`, which only worked
  when system uptime exceeded the interval.
- A failed lake pull could push a single window over the full Silver history;
  pulls now fail the job, and serving tables load into a shadow table and swap
  atomically.
- Double counting between OpenSky movements and AirLabs schedules; unknown
  delays are kept as NULL instead of being counted as on-time flights.
- Timezone drift when casting `TIMESTAMPTZ` to `TIMESTAMP` in the serving
  watermark, which re-read old rows and skipped boundary ones.
- Airport reference gaps, missing route distances, and `dim_airport.type`
  reading the wrong source field.
- NULL aggregate crashes in the story builders and the airport leaderboard.
- Live map cost: viewport culling, pixel-ratio cap, and a cheaper globe to
  mercator switch; forecast rows keyed per station-hour instead of collapsing
  to a single hour; flight history bounded by age and size.

Archived on Zenodo: version DOI [10.5281/zenodo.22790202](https://doi.org/10.5281/zenodo.22790202),
concept DOI [10.5281/zenodo.22790201](https://doi.org/10.5281/zenodo.22790201).

[Unreleased]: https://github.com/swadhinbiswas/eu-air-traffic/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/swadhinbiswas/eu-air-traffic/releases/tag/v0.1.0
