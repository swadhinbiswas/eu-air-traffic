# Deploying EU Air Traffic on AWS

This is the runbook. It takes the repository as it is today and stands the same
pipeline up on AWS: a collector on ECS, MSK for the bus, S3 + Iceberg for the
lake, a scheduled lake job, Redshift for the warehouse, Aurora for the serving
copy, and S3 + CloudFront for the dashboard.

It is a **reference implementation, not the live system**: none of these
resources are currently provisioned. The running deployment is the free-tier
stack described in the README; this document exists to show that the same code
scales without a rewrite.

Read [`aws-architecture.md`](aws-architecture.md) first for *why* each service
was chosen. This document is *how*.

> **Reality check.** The current stack runs on free tiers. The AWS stack does
> not. The two line items that surprise people are **MSK Serverless** and
> **Redshift Serverless**, both of which have a monthly minimum. If cost is the
> constraint, use the cheap variant noted in each section (plain EC2 + S3 +
> Athena) — the application code is identical either way.

## Reference implementation

The code this runbook describes already exists in the repository:

| Artefact | What it is |
|---|---|
| `infra/terraform/` | The whole stack as a Terraform root module (network, ECR, secrets, S3, Glue, MSK, ECS, ALB, Step Functions, EventBridge, Aurora, Redshift, CloudFront, alarms). `infra/terraform/README.md` has the usage. |
| `.github/workflows/deploy-aws.yml` | Keyless CI deploy: OIDC role assumption, `terraform plan`/`apply`, arm64 image builds pushed to ECR, ECS rollout. |
| `services/lake_backend.py` | The lake abstraction (`hf` \| `s3` \| `local`). `LAKE_BACKEND=s3` moves Bronze/Silver onto S3 with no change to the callers. |
| `scripts/publish_aurora.py` | The Aurora PostgreSQL serving publisher — shadow-table swap, watermark upserts, `site_summary`, optional RDS IAM auth. |
| `config/settings.py` | The new settings: `LAKE_BACKEND`, `S3_BUCKET`, `S3_PREFIX`, `AWS_REGION`, `DATABASE_URL`, `SERVING_DATABASE_URL`, `AURORA_IAM_AUTH`, `PG_SCHEMA`. |

The rest of this document explains the choices behind that code and the manual
steps a human still has to take.

---

## 0. What changes in the repo, and what doesn't

Almost nothing in the Python changes. The design already isolates the backends.

| Concern | Repo today | AWS | Code change |
|---|---|---|---|
| Kafka auth | `SASL_SSL` + `PLAIN` (Aiven) | `SASL_SSL` + `SCRAM-SHA-512` (MSK) | Env vars only |
| Kafka broker host | `AIVEN_KAFKA_HOST` | MSK bootstrap string | Env vars only |
| Lake | Hugging Face Hub | S3 (+ Glue/Iceberg) | `services/lake_backend.py` (done) |
| Warehouse | MotherDuck | Redshift Serverless (or Athena) | `dbt` target + `WAREHOUSE_TARGET` |
| Serving | Turso | Aurora PostgreSQL reader | `scripts/publish_aurora.py` (done) |
| Cadence | GitHub schedule + VPS dispatcher | EventBridge Scheduler | `infra/terraform/scheduler.tf` (done) |
| Secrets | `.env` on the host | Secrets Manager + task roles | `infra/terraform/secrets.tf` + IAM roles |
| Dashboard host | Cloudflare Pages | S3 + CloudFront | `infra/terraform/cloudfront.tf` |

If you want the smallest possible migration, keep **Hugging Face**, **MotherDuck**
and **Turso** as the three data backends and only move the *compute* (collector
and lake job) onto AWS. That gets you the operational story — managed compute,
secrets, monitoring, IaC — without touching a single storage integration. The
rest of this guide assumes you're moving the whole thing.

---

## 1. Prerequisites

```bash
# Tooling
aws --version                 # v2
terraform --version || cdk --version
docker buildx version
uv --version                  # only for local checks
```

You need, in the target account:

- A **VPC** with at least two private subnets and NAT (or VPC endpoints) for
  ECS tasks to reach MSK, S3, ECR and Secrets Manager.
- Permission to create ECS, MSK, S3, Redshift, Aurora, CloudFront, IAM roles,
  Secrets Manager, EventBridge, CloudWatch and SNS resources.
- An **IAM OIDC provider for GitHub Actions** if you want CI to deploy without
  long-lived keys (recommended; see §11).

I'd drive all of this from Terraform or CDK rather than the console. The CLI
commands below are the readable form of what the IaC should do — use them to
bootstrap and to verify, not as the source of truth.

---

## 2. Secrets and configuration

Everything the app reads comes from environment variables
(`config/settings.py`). On AWS, stop shipping a `.env`; put the values in
Secrets Manager and parameters in a single SSM parameter, then inject them into
the task definitions.

### 2.1 Secrets Manager

Create one secret per workload so a compromise is scoped, not global.

```bash
aws secretsmanager create-secret --name eu-air-traffic/collector \
  --secret-string '{
    "OPENSKY_CLIENT_ID": "...",
    "OPENSKY_CLIENT_SECRET": "...",
    "AIRLABS_API_KEY": "...",
    "AIVEN_KAFKA_USERNAME": "msk-scram-user",
    "AIVEN_KAFKA_PASSWORD": "..."
  }'

aws secretsmanager create-secret --name eu-air-traffic/lake \
  --secret-string '{
    "HF_TOKEN": "...",
    "MOTHERDUCK_TOKEN": "...",
    "TURSO_TARGETS": "[...]"
  }'
```

The collector does **not** need the lake or warehouse credentials, and the lake
job does **not** need the upstream API keys. Keep it that way.

### 2.2 Environment-variable mapping

`.env` name → where it goes on AWS. Unchanged names mean no application edits.

| `.env` variable | AWS value / source |
|---|---|
| `ENVIRONMENT` | `production` (task env) |
| `OPENSKY_CLIENT_ID` / `_SECRET` | Secrets Manager (collector) |
| `AIRLABS_API_KEY` | Secrets Manager (collector) |
| `AIVEN_KAFKA_HOST` | MSK bootstrap brokers, e.g. `b-1.<cluster>.kafka.<region>.amazonaws.com` |
| `AIVEN_KAFKA_PORT` | `9096` for SASL/SCRAM over TLS (or `9092` if you use IAM auth) |
| `AIVEN_KAFKA_USERNAME` / `_PASSWORD` | MSK SCRAM credentials → Secrets Manager |
| `AIVEN_KAFKA_CA_CERT` | Path to the MSK CA `.pem` baked into the image or mounted from S3 |
| `KAFKA_SECURITY_PROTOCOL` | `SASL_SSL` |
| `KAFKA_SASL_MECHANISM` | `SCRAM-SHA-512` |
| `KAFKA_TOPIC_*` | Keep the five defaults (`eu-positions`, `eu-flights`, `eu-weather`, `eu-fuel`, `eu-reference`) |
| `KAFKA_RETENTION_HOURS` | Must exceed the lake cadence; keep `24` |
| `LAKE_WINDOW_SECONDS` | Keep `420`–`540` |
| `HF_TOKEN` / `HF_REPO` | Secrets Manager (lake) — or drop entirely if you move the lake to S3 |
| `MOTHERDUCK_TOKEN` | Secrets Manager (lake) — or Redshift credentials |
| `TURSO_TARGETS` | Secrets Manager (lake) — or Aurora credentials |
| `LAKE_BACKEND` | `s3` (was implicit `hf`); selects `services/lake_backend.py` |
| `AWS_REGION` | e.g. `eu-central-1`; used by the S3 client and RDS IAM auth |
| `S3_BUCKET` / `S3_PREFIX` | The lake bucket and optional root prefix |
| `S3_ENDPOINT_URL` | Unset on AWS; only for LocalStack/MinIO in tests |
| `DATABASE_URL` | Writer DSN for the serving copy, from Secrets Manager |
| `SERVING_DATABASE_URL` | Read-only DSN the dashboard uses (never the writer) |
| `AURORA_IAM_AUTH` | `true` to use a short-lived RDS token instead of a stored password |
| `PG_SCHEMA` | `public` (or a dedicated serving schema) |
| `LIVE_API_HOST` | `0.0.0.0` on Fargate (the ALB terminates TLS) |
| `LIVE_API_PORT` | `8090` (container port) |
| `LIVE_API_PUBLIC_URL` | The ALB/CloudFront URL the dashboard is built against |
| `VITE_LIVE_URL` | Same ALB/CloudFront URL, passed at dashboard build time |
| `VITE_TURSO_URL_n` / `_TOKEN_n` | Replaced by the Aurora read-only reader endpoint + IAM auth, or a read-only token through API Gateway |

> **Kafka auth note.** `kafka-python-ng` supports SASL/SCRAM directly, so MSK
> with **SASL/SCRAM** is a configuration change. MSK **IAM auth** needs a
> separate signer plugin (`aws-msk-iam-sasl-signer-python`) and a custom SASL
> mechanism — that *is* a code change in `services/kafka_bus.py`. Prefer SCRAM
> unless you have a reason not to.

### 2.3 MSK CA certificate

The client expects a file path. Fetch the CA bundle once and bake it into both
images (it's not secret):

```bash
curl -fsS "https://www.amazontrust.com/repository/AmazonRootCA1.pem" \
  -o deploy/aws-msk-ca.pem
# MSK also publishes a cluster-specific CA; either works with TLS.
```

---

## 3. Container images → ECR

Both Dockerfiles in `docker/` work unchanged. They already build on
`python:3.12-slim`, install `uv`, and expose `8090` with a healthcheck.

```bash
REGION=eu-central-1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

aws ecr create-repository --repository-name eu-air-traffic/collector
aws ecr create-repository --repository-name eu-air-traffic/lake-job

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

docker buildx build --platform linux/arm64 \
  -f docker/Dockerfile.collector -t "$REGISTRY/eu-air-traffic/collector:$(git rev-parse --short HEAD)" --push .

docker buildx build --platform linux/arm64 \
  -f docker/lake-job.Dockerfile -t "$REGISTRY/eu-air-traffic/lake-job:$(git rev-parse --short HEAD)" --push .
```

Build **arm64** and run on **Graviton** Fargate: roughly 20% cheaper for the
same work, and this workload is CPU-light and I/O-heavy.

The lake image's `ENTRYPOINT` is `bash scripts/run_lake.sh`, so an ECS task can
run it with no command override.

---

## 4. The lake on S3

`warehouse/{bronze,silver,gold}` maps to S3 prefixes. One bucket, three prefixes,
versioning on, lifecycle to Glacier for old Bronze.

```bash
aws s3api create-bucket --bucket eu-air-traffic-lake --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"
aws s3api put-bucket-versioning --bucket eu-air-traffic-lake \
  --versioning-configuration Status=Enabled
```

Layout to mirror the local Medallion tree:

```
s3://eu-air-traffic-lake/
  bronze/ingest_date=YYYY-MM-DD/<source>/…
  silver/<entity>/…            # eleven snapshots, partitioned by entity + date
  gold/…
  checkpoints/                 # quality report, watermarks, route memory
```

Register the Silver and Gold prefixes in the **Glue Data Catalog**:

```bash
aws glue create-database --database-input Name=eu_air_traffic
# Crawler for S3 Parquet, or create the tables explicitly once the schema is stable
aws glue create-crawler --name eu-air-traffic-silver \
  --database-name eu_air_traffic --role <glue-role-arn> \
  --targets '{"S3Targets":[{"Path":"s3://eu-air-traffic-lake/silver/"}]}'
```

For Iceberg (recommended once you want `MERGE` and deletes), add the Iceberg
table properties and use the Glue catalog as the metastore; Athena can query it
immediately.

**Implemented:** `services/lake_backend.py` is the storage abstraction
(`hf` | `s3` | `local`), selected by `LAKE_BACKEND`. Both writers
(`services/sink.py`, `scripts/lake_sync.py`) already go through it, including
the "only push changed files" behaviour — the S3 backend compares the local size
against the remote `ContentLength` and skips the upload when they match. Swapping
in an Iceberg writer is a change to that one module.

### 4.1 Where does the DuckDB file live?

It doesn't need to persist. The GitHub job is already ephemeral: it pulls
Silver, builds, publishes. On AWS the task does the same — pull Silver from S3
into ephemeral disk, build DuckDB, run dbt, publish marts. If you'd rather keep
a warm warehouse between runs, mount **EFS** at `/app/warehouse`, but note the
single-writer rule: **one ECS task at a time** (EventBridge + a concurrency
guard, or a Step Functions lock), because DuckDB and Iceberg both assume a
single writer per table.

---

## 5. Event bus → MSK

```bash
aws kafka create-cluster \
  --cluster-name eu-air-traffic \
  --kafka-version 3.6.0 \
  --number-of-broker-nodes 3 \
  --broker-node-group-info '{
    "InstanceType":"kafka.t3.small",
    "ClientSubnets":["subnet-a","subnet-b","subnet-c"],
    "SecurityGroups":["sg-msk"],
    "StorageInfo":{"EBSStorageInfo":{"VolumeSize":100}}
  }' \
  --encryption-info '{"EncryptionInTransit":{"ClientBroker":"TLS"}}' \
  --client-authentication '{"Sasl":{"Scram":{"Enabled":true}}}'
```

Then create the five topics explicitly — do not rely on auto-create:

```bash
BOOTSTRAP=$(aws kafka get-bootstrap-brokers --cluster-arn <arn> --query BootstrapBrokerStringSaslScram --output text)

for t in eu-positions eu-flights eu-weather eu-fuel eu-reference; do
  kafka-topics.sh --bootstrap-server "$BOOTSTRAP" \
    --command-config client-scram.properties \
    --create --if-not-exists --topic "$t" --partitions 6 --replication-factor 3 \
    --config retention.ms=$((24*3600*1000)) --config cleanup.policy=delete
done
```

Partitions: start at 6 so you can later run several collectors in one consumer
group with per-aircraft ordering. The collector keys records by `icao24` /
`flight_id` already.

Security group: allow the ECS tasks' SG on **9096** (SCRAM/TLS) only. Nothing
else should reach the brokers.

---

## 6. The collector on ECS

One service, or — better — **one service per source class** so a rate-limited
upstream can't delay the rest. The code already runs each source on its own
interval; splitting is a deployment decision, not a refactor.

For the fastest path, one service running `services.collector` is equivalent to
today's VPS.

### 6.1 Task definition (sketch)

```json
{
  "family": "eu-air-traffic-collector",
  "cpu": "1024", "memory": "2048",
  "runtimePlatform": { "cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX" },
  "executionRoleArn": "arn:aws:iam::<acct>:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::<acct>:role/eu-air-traffic-collector-task",
  "containerDefinitions": [{
    "name": "collector",
    "image": "<registry>/eu-air-traffic/collector:<sha>",
    "essential": true,
    "portMappings": [{ "containerPort": 8090 }],
    "environment": [
      { "name": "ENVIRONMENT", "value": "production" },
      { "name": "KAFKA_SECURITY_PROTOCOL", "value": "SASL_SSL" },
      { "name": "KAFKA_SASL_MECHANISM", "value": "SCRAM-SHA-512" },
      { "name": "LIVE_API_HOST", "value": "0.0.0.0" },
      { "name": "AIVEN_KAFKA_CA_CERT", "value": "/app/deploy/aws-msk-ca.pem" }
    ],
    "secrets": [
      { "name": "OPENSKY_CLIENT_ID", "valueFrom": "arn:aws:secretsmanager:...:secret:eu-air-traffic/collector:OPENSKY_CLIENT_ID::" },
      { "name": "AIVEN_KAFKA_USERNAME", "valueFrom": "...:AIVEN_KAFKA_USERNAME::" },
      { "name": "AIVEN_KAFKA_PASSWORD", "valueFrom": "...:AIVEN_KAFKA_PASSWORD::" }
    ],
    "healthCheck": {
      "command": ["CMD-SHELL", "curl -fsS http://127.0.0.1:8090/health || exit 1"],
      "interval": 30, "timeout": 5, "retries": 3, "startPeriod": 20
    },
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/eu-air-traffic-collector",
        "awslogs-region": "eu-central-1",
        "awslogs-stream-prefix": "collector"
      }
    }
  }]
}
```

The task role needs only: read its own secret, write logs. Nothing else.

### 6.2 Service

- Launch type **FARGATE**, desired count 1 (or one per source class).
- No public IP; egress via NAT or VPC endpoints.
- Register the task in a target group on port `8090`.

### 6.3 Live API

The collector serves `/live/snapshot`. Put an **ALB** in front:

- Listener `443` with an ACM certificate (e.g. `live.example.com`).
- Target group → the collector tasks, health check `/health`.
- `LIVE_API_PUBLIC_URL=https://live.example.com` in the task env.

If you split the API from the collectors (recommended once you scale), run a
second ECS service with the same image and a command override that starts only
the API, and put shared snapshot state in **ElastiCache (Redis)** or
**DynamoDB** with a TTL. The `LiveStore` is in-memory today, so multi-replica
serving is the one place that needs real code.

**Cheap alternative:** one `t4g.small` EC2 with `deploy/eu-collector.service`
and `deploy/Caddyfile` transfers almost verbatim. Caddy becomes the ALB's job if
you go the Fargate route.

---

## 7. The lake job

Today: `.github/workflows/lake.yml` + `scripts/run_lake.sh`. On AWS, two options.

### 7.1 Step Functions + EventBridge (least change)

1. Create an ECS task definition from `docker/lake-job.Dockerfile`.
2. Wrap it in a **Step Functions** state machine. Each step of
   `run_lake.sh` becomes a task, or run the whole script as one task and let
   Step Functions own retries and alerting:

```json
{
  "StartAt": "RunLakeCycle",
  "States": {
    "RunLakeCycle": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
      "Parameters": {
        "Cluster": "eu-air-traffic",
        "TaskDefinition": "eu-air-traffic-lake",
        "LaunchType": "FARGATE",
        "NetworkConfiguration": {
          "AwsvpcConfiguration": {
            "Subnets": ["subnet-a", "subnet-b"],
            "SecurityGroups": ["sg-lake"],
            "AssignPublicIp": "DISABLED"
          }
        }
      },
      "Retry": [{ "ErrorEquals": ["States.TaskFailed"], "MaxAttempts": 2, "BackoffRate": 2.0 }],
      "Catch": [{ "ErrorEquals": ["States.ALL"], "Next": "AlertOnFailure" }],
      "Next": "Done"
    },
    "AlertOnFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": { "TopicArn": "arn:aws:sns:...:eu-air-traffic-alerts", "Message": "Lake cycle failed" },
      "End": true
    },
    "Done": { "Type": "Succeed" }
  }
}
```

3. Trigger it every 15 minutes with **EventBridge Scheduler**:

```bash
aws scheduler create-schedule \
  --name eu-air-traffic-lake \
  --schedule-expression "rate(15 minutes)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target '{"Arn":"arn:aws:states:...:stateMachine:eu-air-traffic-lake","RoleArn":"arn:aws:iam::...:role/scheduler-invoke-sfn"}'
```

This single change replaces both the GitHub `schedule` **and** the whole
`deploy/eu-lake-dispatch.*` workaround for GitHub's best-effort scheduler.
EventBridge is reliable, and `ecs:runTask.sync` gives you a real failure signal.

**Single writer:** set the state machine or the ECS service to at most one
concurrent execution. The repo's concurrency group
(`cancel-in-progress: false`) exists for exactly this reason.

### 7.2 MWAA (when you need backfills)

If you want scheduled backfills, SLAs, and a lineage UI, put the same steps in
an **MWAA** DAG (Airflow) or Dagster/Prefect on ECS. The README already names
this as the intended production path. Don't do it early — Step Functions is
enough until you're actually replaying history.

---

## 8. Warehouse

Replace MotherDuck with whichever matches your query shape.

### 8.1 Redshift Serverless

```bash
aws redshift-serverless create-namespace --namespace-name eu-air-traffic \
  --admin-username admin --admin-user-password "$(openssl rand -base64 24)"
aws redshift-serverless create-workgroup --workgroup-name eu-air-traffic \
  --namespace-name eu-air-traffic --base-capacity 8 --publicly-accessible false
```

- Add `dbt-redshift` to the lake image and point `DBT_TARGET=redshift` at the
  workgroup endpoint.
- The dbt models move over with materialisation changes; the repo's
  snapshot-replacement pattern becomes an incremental model with a `unique_key`.
- Store the password in Secrets Manager, not in `profiles.yml`.

### 8.2 Athena over Iceberg (cheaper)

Skip a standalone warehouse entirely: the Gold layer in §4 *is* the warehouse,
queryable with Athena. Set the dbt `athena` adapter, or register the Gold tables
as views and let the API query them. Cheaper at low volume, slower for heavy
joins.

Either way, keep the publish strictly **build locally → publish atomically**.
The README's "growing the lake" section describes the exact upgrade:
`OPTIMIZE`/compaction on a schedule, and MERGE on a key instead of full replace.

---

## 9. Serving layer

This is the security-sensitive piece. Today Turso gives the browser a token that
can read and cannot write. Preserve that property.

### 9.1 Aurora PostgreSQL (recommended)

- **Aurora Serverless v2** cluster, PostgreSQL-compatible, Multi-AZ.
- One **writer** for `scripts/publish_turso.py`'s replacement.
- One **reader endpoint** for the dashboard.
- **IAM database authentication** with a read-only role; the dashboard gets
  short-lived credentials via an API Gateway/ Lambda authorizer, never a static
  password in the bundle.

**Implemented:** `scripts/publish_aurora.py` is the Aurora publisher
(`SERVING_BACKEND=aurora` in `scripts/run_lake.sh`). It keeps the good parts of
the Turso design:

- Publish into a **shadow table, then swap atomically** in a Postgres
  transaction (readers on the reader endpoint never see an empty table).
- Precompute the single-row `site_summary` so browser paging never scans facts.
- Drop tables the site no longer needs (`DEPRECATED_TABLES`, shared with the
  Turso publisher so the two stay in lockstep).
- Sync growing tables by watermark; upload statics only on a content-hash change
  and past the per-table refresh floor.
- Use a short-lived **RDS IAM auth token** (`AURORA_IAM_AUTH=true`) so no
  password is stored in the task.

### 9.2 The browser token problem

A React app can't safely hold IAM credentials. Two clean options:

1. **API Gateway + Lambda** exposing read-only endpoints (snapshot, page,
   workbench query). The Lambda has the DB credential; the browser only sees a
   URL. Add per-IP rate limits and a hard row/time cap on the workbench query —
   the README already calls for both.
2. **Cognito Identity** with an unauthenticated role that has `rds-db:connect`
   for the read-only DB user, if you want the browser to talk to Aurora
   directly.

Option 1 is the one I'd pick, and it also lets you cache at the edge.

### 9.3 Edge cache

Put **CloudFront** in front of the API and the dashboard:

- ETag / `304` handling on historical endpoints.
- Immutable, hashed asset paths.
- `Cache-Control: no-store` for `/live/snapshot` — the collector already sets
  this header; don't let CloudFront override it.

---

## 10. Dashboard → S3 + CloudFront

```bash
aws s3api create-bucket --bucket eu-air-traffic-web --region "$REGION" ...
aws cloudfront create-distribution --origin-domain-name eu-air-traffic-web.s3.amazonaws.com ...
```

Build with the AWS endpoints baked in (they're inlined at build time — the same
caveat as Cloudflare Pages, so a config change needs a redeploy):

```bash
cd web
VITE_LIVE_URL=https://live.example.com \
VITE_API_URL=https://api.example.com \
npm ci && npm run build
aws s3 sync dist/ s3://eu-air-traffic-web/ --delete \
  --cache-control "public,max-age=31536000,immutable" --exclude "index.html" --exclude "*.json"
aws s3 cp dist/index.html s3://eu-air-traffic-web/index.html \
  --cache-control "no-cache"
aws cloudfront create-invalidation --distribution-id <id> --paths "/" "/index.html"
```

Use **Amplify Hosting** instead if you want a preview deployment per pull
request — the README's production-swap line asks for exactly that.

---

## 11. CI/CD

Keep GitHub Actions as the trigger, but drop long-lived AWS keys:

1. Create an IAM OIDC provider for `token.actions.githubusercontent.com`.
2. Create a role trusted only from your repo's `main` branch, with permission to
   push to ECR and update ECS/Step Functions.
3. In `.github/workflows/`, replace the `uv run …` lake steps with:

```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::<acct>:role/github-eu-air-traffic
    aws-region: eu-central-1
- run: |
    docker buildx build -f docker/lake-job.Dockerfile -t "$REGISTRY/eu-air-traffic/lake-job:$GITHUB_SHA" --push .
    aws ecs update-service --cluster eu-air-traffic --service eu-air-traffic-lake --force-new-deployment
```

Or move to **CodePipeline/CodeBuild** entirely. The existing `ci.yml` (tests,
lint, type-check) stays as-is; only the deploy workflow changes.

---

## 12. Observability

The quality report already computes per-source freshness
(`pipelines/quality.py`), so alarm on that rather than on process liveness:

- **CloudWatch Logs** — one log group per service (already in the task defs).
- **CloudWatch alarms**:
  - Collector: `/health` failing for 2 consecutive periods.
  - MSK: consumer lag (`OffsetLag`) on the sink group above threshold.
  - Lake: Step Functions execution failed (EventBridge → SNS).
  - Lake: last successful cycle older than 30 minutes.
  - Data freshness per source: `scripts/publish_metrics.py` runs at the end of
    every lake cycle (`LAKE_METRICS_ENABLED=1`) and emits
    `EUAirTraffic/Lake/LastSuccessfulCycleAgeSeconds` via `PutMetricData`, which
    the freshness alarm watches.
- **SNS topic** `eu-air-traffic-alerts` → email/Slack.
- **ADOT** (AWS Distro for OpenTelemetry) sidecar for traces, and **Amazon
  Managed Grafana + Managed Prometheus** if you want the dashboards the README
  imagines.

---

## 13. Verification checklist

Work through this in order; each step has a clear pass/fail.

1. **Images** — `docker run --rm <collector> python -m services.collector --once`
   with mock mode succeeds locally.
2. **Secrets** — the collector task starts and logs `credentials_available` with
   OpenSky/AirLabs true.
3. **Kafka** — create topics; the collector produces; `kafka-consumer-groups.sh
   --describe` shows the sink group's offsets advancing.
4. **Lake** — run the Step Functions state machine by hand; confirm new objects
   under `s3://…/bronze/`, Silver updated, dbt tests pass, marts published.
5. **Warehouse** — query a Gold table (Redshift editor or Athena) and sanity
   check row counts against the quality report.
6. **Serving** — hit the read-only endpoint with the browser token; confirm a
   write attempt is rejected.
7. **Live API** — `curl https://live.example.com/health`; the map renders and
   aircraft move.
8. **Dashboard** — analytics pages match the warehouse.
9. **Alarms** — force a failure (bad secret) and confirm SNS fires.
10. **Scale** — only now raise collector replicas and lake concurrency.

---

## 14. Failure modes to watch

These are the ones this project has already met — expect them again on AWS.

| Symptom | Likely cause | Fix |
|---|---|---|
| Collector connects but produces nothing | MSK CA cert path wrong, or SCRAM mechanism mismatch | Check `AIVEN_KAFKA_CA_CERT` and `KAFKA_SASL_MECHANISM=SCRAM-SHA-512` |
| Records vanish between cycles | MSK retention shorter than the lake cadence | `retention.ms` > `LAKE_WINDOW_SECONDS × 1000` |
| Duplicate rows in Silver | Two lake tasks ran at once | One writer; enforce via Step Functions/ECS concurrency |
| Lake "succeeds" but nothing published | The repo's old silent-success bug | Publishers must fail non-zero; the current code already does |
| Dashboard frozen on one snapshot | CloudFront caching `/live/snapshot` | Respect the app's `Cache-Control: no-store` |
| Stale analytics, healthy processes | Upstream key expired, not a crash | Alarm on **freshness**, not liveness |
| Surprise bill | MSK Serverless / Redshift Serverless minimums | Use the cheap variant if volume doesn't justify them |

---

## 15. Cost sketch (eu-central-1, order of magnitude)

| Component | Cheap variant | Managed variant |
|---|---|---|
| Collector | 1× `t4g.small` EC2 ~ $12/mo | 1–2 Fargate tasks ~ $25–50/mo |
| Event bus | — | MSK Serverless from ~ $50/mo |
| Lake | S3 ~ $1–5/mo | S3 + Glue ~ $5–20/mo |
| Lake job | ECS Fargate task 15-min cadence ~ $10–20/mo | same |
| Warehouse | Athena per-query, ~ $1–10/mo | Redshift Serverless from ~ $100+/mo |
| Serving | Aurora Serverless v2 scales to ~0, ~ $15–40/mo | Aurora provisioned higher |
| Dashboard | S3 + CloudFront ~ $1–5/mo | Amplify ~ $5–15/mo |
| Secrets/alarms | ~ $1–3/mo | ~ $5–15/mo |

Cheap stack lands somewhere around **$50–100/month**. The managed stack is
closer to **$250–400/month**, dominated by MSK and Redshift. Neither is a
free-tier bill, so decide deliberately.

---

## 16. Teardown

Everything is disposable, but order matters or you'll leave billable orphans:

```bash
aws ecs update-service --cluster eu-air-traffic --service eu-air-traffic-collector --desired-count 0
aws scheduler delete-schedule --name eu-air-traffic-lake
aws kafka delete-cluster --cluster-arn <arn>
aws redshift-serverless delete-workgroup --workgroup-name eu-air-traffic
aws redshift-serverless delete-namespace --namespace-name eu-air-traffic
aws rds delete-db-cluster --db-cluster-identifier eu-air-traffic --skip-final-snapshot
# Then the remaining ECS services, ECR repos, S3 buckets, and the CloudFront distribution.
```

Keep the S3 lake bucket — it's the only thing here worth backing up. Enable
versioning and a lifecycle rule before you ever need it.

---

## Appendix — resources to create (IaC inventory)

```
networking/   VPC, 2+ private subnets, NAT or endpoints, security groups
iam/          task roles, execution role, scheduler role, GitHub OIDC role
secrets/      collector secret, lake secret
ecr/          collector, lake-job
ecs/          cluster, collector service+task, lake task definition
kafka/        MSK cluster, 5 topics, SCRAM user
s3/           eu-air-traffic-lake (versioned), eu-air-traffic-web
glue/         eu_air_traffic database, Silver/Gold catalog tables
stepfunctions/ eu-air-traffic-lake state machine
events/       EventBridge Scheduler rule (rate 15 minutes)
redshift/     namespace + workgroup          (or Athena only)
aurora/       Serverless v2 cluster + reader endpoint
cloudfront/   web distribution, api distribution
cloudwatch/   log groups, freshness/lag alarms
sns/          eu-air-traffic-alerts
```
