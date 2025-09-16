# EU Air Traffic

<p align="center">
  <img src="images/logo.svg" width="96" alt="EU air traffic logo" />
</p>

[![CI](https://github.com/swadhinbiswas/eu-air-traffic/actions/workflows/ci.yml/badge.svg)](https://github.com/swadhinbiswas/eu-air-traffic/actions/workflows/ci.yml)
[![Lake](https://github.com/swadhinbiswas/eu-air-traffic/actions/workflows/lake.yml/badge.svg)](https://github.com/swadhinbiswas/eu-air-traffic/actions/workflows/lake.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Live dashboard: https://airtraffic-eu.pages.dev
· Live API: https://vps.swadhin.cv/health
· Demo video: coming soon

I built this to watch European airspace without spending anything: live
aircraft positions, weather, schedules, delays, emissions, and Eurostat's
official passenger numbers as a benchmark. The free tiers are the whole game
here, so quotas and budgets drive most of the design decisions below.

---

## What it does

| Layer | What you get |
|---|---|
| Live map | ~2,000 aircraft with callsign, type, altitude, speed, vertical rate, route lines, METAR/TAF stations, searchable by callsign/registration/type/airport |
| Live analytics | Airspace composition, altitude bands, operators, carbon intensity (OpenAP kinematic model, measured vs. estimated) |
| Business analysis | Delays, punctuality, cancellations, airport leaderboard, airline rankings, route performance, weather impact, seasonal trends |
| Official benchmark | Eurostat monthly passengers per airport cross-checked against the movements this platform observed |
| SQL workbench | Query the serving copy directly in the browser (read-only token) |
| Ops | Pipeline step report, data-quality report, dbt lineage, freshness |

## Architecture

![EU air traffic architecture: a VPS collector publishes to Kafka, a scheduled pipeline builds bronze, silver and dbt marts, and Hugging Face, MotherDuck and Turso serve the dashboard.](images/eu-air-traffic.png)

A collector on a small VPS polls upstream APIs and publishes to a five-topic
Kafka cluster. Every 15 minutes a scheduled pipeline drains Kafka, builds
Bronze/ Silver/ DuckDB layers and dbt marts, then pushes to Hugging Face (the
dataset lake), MotherDuck (the full warehouse) and Turso (the copy the browser
is allowed to read). The dashboard reads Turso plus the collector's live API.

### Why two serving stores

MotherDuck holds the full warehouse, but its free plan cannot issue scoped
read-only tokens. Turso can, so the browser reads a derived, bounded copy
there. Cheap to hold, safe to expose, refreshed every cycle.

## Zero-cost constraints (and how they shaped the design)

| Constraint | Design response |
|---|---|
| Kafka free plan: 5 topics only | One topic per domain; weather (metar/taf/forecast) and reference data multiplex with a `_kind` discriminator the sink splits back into datasets |
| OpenSky: credit budgets per endpoint | `/flights/all` (one request, both ends) + live departures for 4 hubs + a nightly arrivals backfill; ~2.7k of 4k daily credits |
| AirLabs: 1,000 calls/month, 50 rows/call | Rotating hubs, persisted monthly counter that stops at the budget, IATA→ICAO resolved from bundled data (no extra calls) |
| Turso: row-write budget | Statics upload only when a content hash changes; positions have a refresh floor; growing tables are watermark-synced |
| Hugging Face: storage | Silver is partitioned per source; only changed files are pushed; unchanged files are recognised as no-ops |
| Runner minutes | Public repo: free. Frontend-only pushes skip the lake entirely |
| VPS: 2 cores | The box only collects; all transformation runs in CI |

## Data sources

- **OpenSky Network** — flight movements (OAuth2 client credentials; username/password is no longer accepted by OpenSky).
- **adsb.lol** — primary live positions; **airplanes.live** and **OpenSky** as fallbacks.
- **AirLabs** — live schedules with planned times and delays.
- **aviationweather.gov** (METAR/TAF) and **Open-Meteo** (forecast).
- **Eurostat `avia_paoa`** — official monthly passengers per airport (~2-month lag), fetched monthly by GitHub Actions and loaded as a benchmark layer.
- **OurAirports / OpenFlights / ICAO 8643** — reference data built once by `scripts/build_reference_data.py`.
- **OpenAP** (TU Delft) — kinematic fuel-flow model, precomputed into a 37-type lookup grid.

## Repository layout

```
├── services/            # VPS runtime: collector, sources, live API/store, Kafka bus, sink
│   └── sources/         #   one module per upstream data product
├── pipelines/           # silver (incremental), warehouse (star schema), quality, orchestrator
├── dbt/                 # staging → intermediate → marts → reports + tests
├── scripts/             # lake_sync, publish_{motherduck,turso,site_tables}, fetch_eurostat, …
├── web/                 # React + Vite dashboard (Turso + live API)
├── deploy/              # systemd unit, Caddyfile
├── docker/              # lake job image (runs the cycle anywhere)
└── tests/               # unit + e2e (pytest)
```

## Local development

```bash
uv sync --extra dev --extra stream --extra turso
cp .env.example .env                      # fill in what you have; several sources work keyless
uv run pytest                             # 120+ tests
uv run python -m services.collector       # optional: run the collector + live API
cd web && npm ci && npm run dev           # dashboard (web/.env.local for VITE_* values)
```

Run the pipeline locally without Kafka:

```bash
MOCK_MODE=true uv run python -m pipelines.orchestrator
AIR_TRAFFIC_DUCKDB_PATH=$PWD/warehouse/air_traffic.duckdb uv run dbt build --project-dir dbt --profiles-dir dbt
```

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `AIVEN_KAFKA_HOST/PORT/USERNAME/PASSWORD/CA_CERT` | Kafka (SASL_SSL) |
| `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` | OpenSky OAuth2 (required) |
| `AIRLABS_API_KEY` | Schedules; the source is skipped without it |
| `HF_TOKEN`, `HF_REPO` | Dataset lake |
| `MOTHERDUCK_TOKEN`, `MOTHERDUCK_DATABASE` | Warehouse |
| `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` | Serving copy (publisher uses the read-write token) |
| `VITE_LIVE_URL`, `VITE_TURSO_URL`, `VITE_TURSO_TOKEN` | Dashboard build-time values; the Turso token must be read-only |

Sane defaults, override only if needed: `POSITIONS_INTERVAL_SECONDS=15`,
`POSITIONS_PUBLISH_INTERVAL_SECONDS=300`, `FLIGHTS_INTERVAL_SECONDS=1800`,
`FLIGHTS_LOOKBACK_MINUTES=90`, `FLIGHTS_ARRIVAL_INTERVAL_SECONDS=21600`,
`AIRLABS_HUBS_PER_CYCLE=4`, `AIRLABS_MONTHLY_BUDGET=800`, `LAKE_WINDOW_SECONDS=420`.

## Deployment

- **Collector (VPS):** `deploy/eu-collector.service` + `deploy/Caddyfile`
  (TLS termination, gzip, binding the API to `127.0.0.1`).
- **Lake (GitHub Actions):** `.github/workflows/lake.yml` — every 15 minutes and
  on data/pipeline pushes; drains Kafka, builds Silver, runs dbt, publishes to
  Hugging Face, MotherDuck and Turso.
- **Eurostat benchmark:** `.github/workflows/eurostat.yml`, monthly.
- **Reliable cadence:** GitHub's scheduler is best-effort. `deploy/eu-lake-dispatch.{service,timer}`
  dispatches the lake from the VPS every 15 minutes (skipping when one is already
  queued), so data keeps arriving even when `schedule` runs late.
- **Dashboard:** `.github/workflows/bundle.yml` + Cloudflare Pages Git integration.
  Set the four `VITE_*` values in Pages → Settings → Variables and secrets (Production).
- **Anywhere else:** `docker/lake-job.Dockerfile` + `scripts/run_lake.sh` runs one
  full cycle in a container (set `HF_SYNC=0` when the dataset is mounted).

### Deploy with Docker (one command)

```bash
cp .env.example .env      # fill in your credentials
./deploy.sh               # collector + lake; add --tls to put Caddy in front
```

`docker-compose.yml` wires the collector (live API on `:8090`, healthchecked),
the lake cycle on a timer — sharing the same `.env` and DuckDB state — and an
optional Caddy TLS front via `SITE_ADDRESS`. `./deploy.sh --logs`, `--ps` and
`--down` manage the stack. No local Kafka is needed: point `.env` at Aiven (or
any broker) and the containers connect out.

## Reliability engineering: what broke and what changed

This platform runs unattended on free infrastructure, and most of the work went
into failure handling rather than the happy path. Each item below has a test:

- **Silent success.** Steps logged an error and exited 0 (an HF upload rejected
  by a trailing space in a configured repo id; a Turso publish that never
  wrote). Failures are now loud, and credentials that are set but empty raise.
- **Fresh-boot assumptions.** A publish cadence compared `monotonic()` against
  `0.0`, which only works when system uptime exceeds the interval. Missing
  state now means "due".
- **Destructive retries.** A failed lake pull could push a single window over
  the full Silver history; publishers could wipe serving tables mid-run. Pulls
  fail the job, and static tables load into a shadow table and swap atomically.
- **Double counting.** OpenSky movements and AirLabs schedules describe the same
  flight with different ids; marts dedupe on callsign + date + endpoint and
  only average delays that are actually known.
- **Timezone traps.** Casting `TIMESTAMPTZ` to `TIMESTAMP` shifts by the session
  offset, silently re-reading old rows and skipping boundary ones.
- **Queue starvation.** Every push entered one single-writer concurrency group
  until frontend-only pushes were excluded and the cadence was widened.

## Tests

```bash
uv run pytest -q                                    # unit + integration paths
uv run dbt build --project-dir dbt --profiles-dir dbt   # 103 models and tests
cd web && npm run build                              # type-check + production build
```

## License

MIT — see [LICENSE](LICENSE).
