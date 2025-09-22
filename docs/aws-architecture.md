# EU Air Traffic, described — and how I'd run it on AWS

Two things in this document:

1. **What this project actually is**, in plain terms, without the marketing.
2. **How I'd do the same thing on AWS** if I had to, component by component, and
   why — written the way I'd explain it to another engineer rather than as a
   service wishlist.

---

## Part 1 — What this repository is

### One sentence

It continuously collects live and historical European air-traffic data from a
handful of free upstream APIs, cleans it through a Bronze → Silver → Gold
pipeline, and serves it as a live map, a set of analytics pages, and a
queryable read-only warehouse copy.

### The shape of it

There are really only four moving parts, and everything else supports them:

1. **A collector.** One long-lived process, designed to sit on a small VPS. It
   polls every upstream (adsb.lol and OpenSky for positions, OpenSky for
   movements, AirLabs for schedules, aviationweather.gov for METAR/TAF,
   Open-Meteo for forecast, Eurostat monthly for passenger benchmarks). It
   normalises each product into one shape and publishes it to Kafka. It also
   keeps the newest snapshot in memory and exposes it over a tiny FastAPI service
   (`/live/snapshot`), which is what the map reads. The browser never talks to
   the upstreams directly — they block browser CORS anyway.

2. **An event bus.** Managed Kafka (Aiven today), **five topics**, one per
   domain. Weather (metar/taf/forecast) and reference data are multiplexed into
   single topics with a `_kind` discriminator that the sink splits back apart.
   This is a deliberate constraint: the free tier only gives you five topics per
   cluster, so the design respects it instead of pretending it doesn't exist.

3. **A scheduled batch job.** Every 15 minutes a GitHub Actions workflow drains
   Kafka from the last consumer offset (not a time window), writes the raw
   records to Parquet as **Bronze**, incrementally builds **Silver** (eleven
   snapshots), runs the star-schema build, then `dbt build` for the marts
   (~103 models and tests). It publishes the lake to Hugging Face, the marts to
   MotherDuck, and a small bounded copy to Turso. The runner keeps its own
   DuckDB file as the working warehouse.

4. **Two serving stores.** MotherDuck holds the *full* warehouse, but it cannot
   hand a browser a scoped read-only token. Turso can, so the dashboard reads a
   derived, deliberately small copy there instead. Paging totals come from a
   precomputed one-row `site_summary`, so the browser never scans fact tables.
   `TURSO_TARGETS` spreads tables across several free accounts and mirrors any
   table listed in more than one, so the site fails over when an account is down
   or out of quota.

The React + Vite dashboard (Cloudflare Pages) reads the collector's live API for
the map and Turso for everything historical.

### What makes it more than a demo

The interesting engineering is all in the constraints and the failure handling:

- **Budgets are enforced in code.** OpenSky's per-endpoint credit budget and
  AirLabs' 1,000 calls/month both have persisted counters that stop at the cap.
  The pipeline would rather go stale than burn a quota and get the key
  cancelled.
- **Failure is loud.** The reliability section of the README lists real bugs that
  were fixed: steps that logged an error and exited 0, retries that could wipe a
  serving table, a publish cadence broken by fresh-boot assumptions.
- **Deduplication across providers.** OpenSky movements and AirLabs schedules
  describe the same flight with different ids; marts dedupe on
  `callsign + date + endpoint`.
- **Cadence is defended twice.** GitHub's scheduler is best-effort, so the VPS
  also dispatches the lake job on a timer.

### The honest summary

It is a genuinely well-scoped data platform that happens to run on free tiers.
The seams are in the right places — every source is an independent module behind
one interface, Kafka decouples collection from processing, the lake is plain
Parquet, dbt owns transformation, and the serving copy is a derived artefact that
can be rebuilt at any time. That last property is what makes it portable: you can
change any single component without rewriting the others.

---

## Part 2 — How I'd do it on AWS

The key point first: **almost none of the application code changes.** This
codebase was written with swappable backends, so moving to AWS is mostly a
substitution of managed services plus one or two genuine rewrites. Below I go
through it the way I'd actually plan it, with the reasoning and the trade-offs.

### 2.1 The collector — EC2 or ECS, not Lambda

The collector is a set of long-lived polling loops with in-memory state (the
live snapshot, watermarks, cooldowns, monthly budget counters). That shape is a
bad fit for Lambda — you'd spend all your time rebuilding state that the current
process just keeps in memory.

So: run it as a container on **ECS Fargate**, one service per source class
(positions, movements, schedules, weather, forecast), behind the same
consumer-group model Kafka already assumes. If I want the absolute cheapest
option and I'm happy babysitting a box, a **t4g.small EC2 instance with
systemd** is almost exactly today's VPS with a different logo — `deploy/eu-collector.service`
transfers nearly verbatim.

For the live API specifically (`/live/snapshot`), I'd put the FastAPI app
behind an **Application Load Balancer** (or API Gateway + a small Lambda proxy
if I want per-request auth), with the collector fleet behind it. Because the
source loops and the API are different scaling profiles, I'd split them: the API
can scale on request count, the collectors on their own health. That split
doesn't exist in the repo today, but the code already separates
`services/collector.py` from `services/live_api.py`, so it's a deployment
change, not a design change.

**Live serving:** the in-memory `LiveStore` doesn't survive restarts or scale
across replicas. If I ran more than one collector I'd push the snapshot to
**ElastiCache (Redis)** or **DynamoDB** with a TTL, so any replica can answer
`/live/snapshot` from a shared, always-fresh cache. That's the one place where
"run more than one copy" forces real work.

### 2.2 The event bus — MSK, or Kinesis if I want to rewrite

**Amazon MSK (or MSK Serverless)** is a drop-in for Aiven Kafka. Five topics
stay five topics; the `_kind` discriminator trick stays because the five-topic
constraint disappears on MSK — I'd be free to split weather into three topics if
I wanted, and honestly I would once the constraint that justified multiplexing
is gone. I'd add **AWS Glue Schema Registry** so producers and consumers can't
drift, and a **dead-letter topic** for records that fail the sink.

The alternative is **Kinesis Data Streams**, which is more AWS-native and has no
broker to manage, but it means rewriting the producer and consumer and giving up
the Kafka ecosystem (offsets, consumer groups, admin APIs) the repo already uses.
I'd only take that trade if I had a strong reason to leave Kafka behind. MSK
keeps `services/kafka_bus.py` essentially unchanged.

**Cadence:** the GitHub `schedule` plus the VPS dispatcher both exist because
GitHub's scheduler is unreliable. On AWS I'd replace both with **EventBridge
Scheduler** firing a 15-minute rule that starts the batch job — and let ECS or
Step Functions own the retry instead of a shell script. That removes the entire
"dispatch only if one isn't already queued" workaround.

### 2.3 The lake — S3 + Iceberg, not Hugging Face

This is the cleanest win. Hugging Face is a fine free dataset host, but S3 with
**Apache Iceberg** (via **AWS Glue Data Catalog** and **Athena**) is the
production answer, and it directly enables the things the README's "Growing the
lake" section already asks for:

- Partition Bronze by ingestion day and Silver by entity + date.
- `MERGE` on a primary key instead of snapshot replacement — that's exactly the
  Iceberg commit the README calls out as the destination.
- Scheduled compaction (`Athena OPTIMIZE` / Glue) to fix the small-file problem
  before it hurts.
- **Lake Formation** for column- and table-level permissions, so the raw Bronze
  layer is locked down while curated marts stay queryable.

The repo already writes every table through one helper, so swapping the writer
for an Iceberg writer lands in one place. If I wanted to stay even closer to the
current code, **plain Parquet on S3 + Glue Catalog + Athena** works with almost
no changes — I'd start there and adopt Iceberg once I needed updates and
deletes.

### 2.4 Transformation — Step Functions + ECS, or MWAA

Today the orchestration is a YAML job and a shell script. On AWS I'd pick
between two things based on how much backfill and lineage I need:

- **Lightweight:** a **Step Functions** state machine whose steps are ECS Fargate
  tasks running the existing Python modules, triggered by EventBridge. Each step
  gets retries and a failure branch (replace the "log an issue" job with an
  **SNS** alert). This is the smallest change — `scripts/run_lake.sh` becomes a
  state machine definition almost line for line.
- **Heavyweight:** **MWAA (Managed Airflow)** or **Dagster/Prefect on ECS** if I
  want real backfills, SLAs, and a scheduling UI. The README explicitly names
  Airflow/Dagster/Prefect/Argo as the production swap, so this is the intended
  growth path.

Two traps I'd watch, both already documented in this repo's reliability section:
**one writer per DuckDB file** (on AWS, one writer per Iceberg table, enforced by
a lock or a single-writer task) and **atomic publishes** (shadow table + swap
becomes a transaction or an Iceberg commit).

### 2.5 The warehouse — Redshift or Athena

MotherDuck is DuckDB-in-the-cloud. AWS equivalents:

- **Amazon Redshift Serverless** if the workload is warehouse-shaped and I want
  dbt with incremental models and a `unique_key` (the README's stated target).
  `dbt-redshift` is mature, and the existing dbt models move over with connection
  and materialisation changes.
- **Athena over Iceberg** if I'd rather not run a warehouse at all — the marts
  become views/queries over the S3 lake, and the warehouse and the lake collapse
  into one thing. Cheaper and simpler, slower for heavy joins.

I'd lean Redshift Serverless for the analytics workload and keep Athena for
ad-hoc/read-only access, which maps neatly onto today's MotherDuck-vs-Turso
split.

### 2.6 The serving layer — Aurora read replica, keep the token model

This is the part people get wrong, so I'd be deliberate about it.

MotherDuck can't issue scoped read-only tokens to a browser; Turso can. The
equivalent on AWS is **Aurora PostgreSQL Serverless v2 with a reader endpoint**,
using **IAM database authentication** and a **read-only role** — that preserves
the exact security property the project depends on: the credential baked into
the browser can read, and cannot write. Alternatively **DynamoDB** with a
read-only IAM policy if the access pattern is narrow and I want single-digit-ms
reads at low cost.

I'd keep the rest of the serving model intact because it's good design
regardless of the database:

- Publish into a **shadow table and swap atomically** (a Postgres transaction
  now).
- Precompute the `site_summary` row so browser paging never scans facts.
- Cache at the edge: **CloudFront** with ETag/304 handling, immutable hashed
  assets, rate limits on the SQL workbench, and a hard row/time cap on any query
  a visitor can trigger.

If I needed Turso's multi-account failover property (the `TURSO_TARGETS`
mirroring), Aurora Global Database or plain multiple read replicas plus the
existing browser-side failover logic covers it — the failover code is in the
dashboard, not the database.

### 2.7 The dashboard — S3 + CloudFront

Static React build. **S3 behind CloudFront** replaces Cloudflare Pages one-to-one,
with **Amplify Hosting** if I want per-pull-request preview deployments (the
README's production-swap line asks for exactly that). The build-time `VITE_*`
values move into the CI pipeline; because they're inlined at build time, a
config change still requires a redeploy — same caveat as Pages, same fix
(deploy hook or push).

### 2.8 Secrets, observability, infrastructure

- **Secrets:** **AWS Secrets Manager** (with rotation) plus **Parameter Store**
  for non-secret config, injected via **ECS task roles / IRSA** rather than a
  `.env` file on the host. Scope each secret to the workload that needs it. This
  directly implements the README's `.env` → Vault/Secrets Manager line.
- **Observability:** **CloudWatch** logs and metrics for everything, with alarms
  on **data freshness per source**, not just process liveness — the quality
  report already computes staleness, so the alarm source exists. Add **AWS
  Distro for OpenTelemetry** for traces and **Amazon Managed Grafana + Managed
  Prometheus** for dashboards. Keep the per-source lag/freshness alerts the
  README calls for; they're the ones that actually catch silent staleness.
- **IaC:** **Terraform** or **AWS CDK** for every resource, with an ECS/EKS
  workload layer. No console-clicking. A staging environment that gets every
  change first.

### 2.9 A cost-honest note

The current stack is free-tier. The AWS version is not, and the two things that
bite are **MSK Serverless's minimum** and **Redshift Serverless's base
capacity**. If I were optimizing for cost over managed-ness, I'd keep S3 +
Athena + Iceberg as both lake and warehouse, run the collector on one Graviton
EC2 box, and only reach for MSK/Redshift when throughput or team size justified
them. The architecture supports that progression because the boundaries are
clean — which is the real thing this project got right.

### 2.10 The deployment order I'd actually follow

1. Provision the substrate: S3 buckets, Glue Catalog, MSK, secrets, VPC.
2. Build the two images (collector and lake job) and push to **ECR**.
3. Deploy the collector; confirm `/health` and that topic offsets advance.
4. Run one lake cycle by hand; confirm Bronze, Silver, dbt marts and the serving
   copy.
5. Point the dashboard at CloudFront + the live API; validate the analytics
   pages against the warehouse directly.
6. Add freshness and lag alarms **before** adding replicas.
7. Scale collectors and lake workers independently — Kafka (or Kinesis) is the
   only thing they share.

---

## The one-line version

This repo is a small, constraint-driven data platform whose seams are already
where a production system needs them. On AWS you'd swap Aiven → MSK, Hugging
Face → S3/Iceberg, MotherDuck → Redshift, Turso → Aurora, Cloudflare Pages →
S3/CloudFront, GitHub Actions → Step Functions/EventBridge, and `.env` →
Secrets Manager — and almost all of the Python, the dbt models, and the failure
handling would come along unchanged.
