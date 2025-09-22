# EU Air Traffic — Terraform (AWS)

This root module provisions the AWS stack described in
[`docs/aws-deployment.md`](../../docs/aws-deployment.md) and
[`docs/aws-architecture.md`](../../docs/aws-architecture.md): the collector on
ECS Fargate, MSK for the event bus, S3 + Glue for the lake, a scheduled lake
job, Redshift Serverless for the warehouse, Aurora PostgreSQL Serverless v2 for
the serving copy, and S3 + CloudFront for the dashboard.

It is one flat root module — no child modules — because the pieces are tightly
coupled and a reader should be able to follow a resource from name to ARN
without jumping between directories. Each file owns one concern.

> **Not free, and not currently deployed.** This is a reference implementation
> kept as code to show the scale path; the live deployment runs on free tiers.
> MSK and Redshift Serverless both have monthly minimums. The architecture doc's
> cheap variant (S3 + Athena, collector on one Graviton EC2) is a different
> stack; this one is the managed one. `enable_redshift = false` drops the
> biggest line item.

---

## Files

| File | Owns |
|---|---|
| `versions.tf` | Terraform/provider pins, backend note |
| `providers.tf` | AWS provider + `default_tags` |
| `variables.tf` | Every input, with defaults |
| `locals.tf` | Naming, tags, derived values |
| `network.tf` | VPC, subnets, NAT, endpoints, security groups |
| `ecr.tf` | Collector and lake-job repositories |
| `secrets.tf` | Collector/lake secrets + the non-secret SSM parameter |
| `s3.tf` | Lake bucket and dashboard bucket |
| `glue.tf` | Catalog database and Silver/Gold crawlers |
| `msk.tf` | MSK cluster, broker config, SCRAM credentials |
| `iam.tf` | Task/execution/orchestration/Glue/GitHub roles |
| `ecs.tf` | Cluster, collector service, lake task definition |
| `alb.tf` | ALB, target group, HTTPS + redirect listeners |
| `stepfunctions.tf` | Lake state machine and its single-writer lock |
| `scheduler.tf` | EventBridge Scheduler 15-minute rule |
| `redshift.tf` | Redshift Serverless namespace + workgroup |
| `aurora.tf` | Aurora Serverless v2 cluster, reader, read-only role |
| `cloudfront.tf` | OAC + distribution + bucket policy |
| `observability.tf` | Log groups, SNS topic, CloudWatch alarms |
| `outputs.tf` | Everything a deploy workflow or an operator needs |

---

## Prerequisites

- **Terraform** >= 1.6 (the module uses `lifecycle { precondition }`).
- **AWS credentials** with permission to create ECS, MSK, S3, Redshift, Aurora,
  CloudFront, IAM, Secrets Manager, EventBridge, CloudWatch and SNS resources.
  The simplest local setup is `aws configure` or `AWS_PROFILE`.
- **An ACM certificate** (optional but recommended):
  - Live API: same region as the ALB (`eu-central-1` by default).
  - Dashboard: **must be in `us-east-1`**, because that is where CloudFront
    looks for its certificates.
- **Docker + buildx** to build the images (or let GitHub Actions do it).
- For the opt-in Aurora read-only-role provisioner only: `psql` on the machine
  running Terraform and network access to Aurora.

State is intentionally not configured here. Before you run `apply` for real,
point the commented `backend "s3"` block in `versions.tf` at a bucket with
locking, or keep local state — but do not commit `terraform.tfstate`; it holds
the generated passwords.

```bash
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars            # at minimum, set alarm_email
```

---

## Usage

```bash
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan  -out tfplan
terraform apply tfplan
```

`terraform output` lists the endpoints, ARNs, bucket names and the two ECR
repository URLs.

### Setting the container image tags

The module references images as `<ecr-repo>:<image_tag>`. On the **first**
apply the repositories are empty, so the collector service will not start until
you push an image. That is expected; do the first image push right after apply
(see below), then update the service.

You have three ways to point the tasks at an image:

1. **Default.** Leave `collector_image`/`lake_image` empty and set `image_tag`
   to the git SHA that CI pushed. `image_tag` defaults to `latest`.
2. **Explicit.** Set `collector_image` and `lake_image` to full URIs
   (including a `@sha256:` digest if you want an immutable reference).
3. **CI-driven.** After `apply`, CI builds with `docker buildx --platform
   linux/arm64` and pushes to the two repository URLs in `terraform output`.
   To make `apply` pick up the new tag, either bump `image_tag` or run:

   ```bash
   aws ecs update-service --cluster "$(terraform output -raw ecs_cluster_name)" \
     --service "$(terraform output -raw collector_service_name)" \
     --force-new-deployment
   ```

Both images are **ARM64** (Graviton Fargate), matching
`docs/aws-deployment.md` §3. Build them with `--platform linux/arm64`.

---

## Post-apply manual steps

These are the steps Terraform deliberately cannot complete for you. Do them in
order.

### 1. Populate the secrets

Terraform creates the secret containers with empty values and then ignores
future changes to their contents, so your values are never reverted:

```bash
# Upstream API keys the collector needs.
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw collector_secret_arn)" \
  --secret-string '{
    "OPENSKY_CLIENT_ID": "...",
    "OPENSKY_CLIENT_SECRET": "...",
    "AIRLABS_API_KEY": "..."
  }'

# Storage / warehouse / serving credentials the lake job needs.
# TURSO_TARGETS is the JSON array described in .env.example.
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw lake_secret_arn)" \
  --secret-string '{
    "HF_TOKEN": "...",
    "MOTHERDUCK_TOKEN": "...",
    "TURSO_TARGETS": "[]"
  }'
```

The Kafka SCRAM credentials, the Redshift admin password and the Aurora master
password are generated by Terraform/RDS; you do not fill those in. Read them
with:

```bash
aws secretsmanager get-secret-value --secret-id "$(terraform output -raw msk_scram_secret_arn)"
aws secretsmanager get-secret-value --secret-id "$(terraform output -raw aurora_master_secret_arn)"
```

### 2. Bake the MSK CA bundle into the images

The client expects a file path (`AIVEN_KAFKA_CA_CERT`). The CA bundle is public
and should be committed at `deploy/aws-msk-ca.pem` so both Dockerfiles COPY it:

```bash
curl -fsS "https://www.amazontrust.com/repository/AmazonRootCA1.pem" \
  -o deploy/aws-msk-ca.pem
```

This file lives outside `infra/terraform/`; add it to `deploy/` in the repo
before building. (Both Dockerfiles copy the repo root with `COPY . .`, so no
Dockerfile change is needed.)

### 3. Build and push both images

```bash
REGISTRY=$(terraform output -raw ecr_collector_repository_url | cut -d/ -f1)
aws ecr get-login-password --region "$(terraform output -raw aws_region)" \
  | docker login --username AWS --password-stdin "$REGISTRY"

TAG=$(git rev-parse --short HEAD)

docker buildx build --platform linux/arm64 \
  -f ../../docker/Dockerfile.collector \
  -t "$(terraform output -raw ecr_collector_repository_url):$TAG" --push ../..

docker buildx build --platform linux/arm64 \
  -f ../../docker/lake-job.Dockerfile \
  -t "$(terraform output -raw ecr_lake_repository_url):$TAG" --push ../..
```

Then roll the collector and/or re-run the task definition with that tag
(`image_tag = "$TAG"` + another `apply`, or `--force-new-deployment` after
registering a task definition revision).

### 4. Create the Kafka topics

The AWS provider has **no `aws_msk_topic` resource**, so Terraform cannot
create topics. The broker config disables auto-create on purpose — run the
repo's admin tool once after the cluster is ACTIVE:

```bash
AIVEN_KAFKA_HOST="$(terraform output -raw msk_bootstrap_brokers_sasl_scram)" \
AIVEN_KAFKA_PORT=9096 \
AIVEN_KAFKA_USERNAME="$(aws secretsmanager get-secret-value --secret-id "$(terraform output -raw msk_scram_secret_arn)" --query SecretString --output text | python -c 'import sys,json;print(json.load(sys.stdin)["username"])')" \
AIVEN_KAFKA_PASSWORD="$(aws secretsmanager get-secret-value --secret-id "$(terraform output -raw msk_scram_secret_arn)" --query SecretString --output text | python -c 'import sys,json;print(json.load(sys.stdin)["password"])')" \
AIVEN_KAFKA_CA_CERT=deploy/aws-msk-ca.pem \
KAFKA_SECURITY_PROTOCOL=SASL_SSL KAFKA_SASL_MECHANISM=SCRAM-SHA-512 \
  python -m scripts.kafka_admin create-topics --partitions 6
```

The five topic names are the same ones wired into both task definitions, so
Terraform and the runtime cannot drift.

### 5. Create the Aurora read-only role

There is no AWS provider resource for a PostgreSQL role. Two options:

- **By hand (default, and what I would do in production).** Connect from a
  bastion with the master credentials and run:

  ```sql
  CREATE ROLE dashboard_reader LOGIN PASSWORD '<from the aurora-reader secret>';
  GRANT CONNECT ON DATABASE air_traffic TO dashboard_reader;
  GRANT USAGE ON SCHEMA public TO dashboard_reader;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_reader;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dashboard_reader;
  ```

  The role name and password are in `terraform output aurora_reader_secret_arn`.

- **With Terraform.** Set `manage_aurora_reader_role = true`. The opt-in
  `local-exec` provisioner (aurora.tf) runs the same SQL with `psql`. It needs
  `psql` and a network path to Aurora from wherever you run `apply`, and it
  fetches the RDS-managed master password at apply time.

The dashboard should authenticate as this role through the **reader** endpoint
using **IAM database authentication** with short-lived credentials, never a
password embedded in the bundle (docs/aws-deployment.md §9).

### 6. First lake cycle by hand

Confirm the batch path before trusting the scheduler:

```bash
aws stepfunctions start-execution \
  --state-machine-arn "$(terraform output -raw sfn_state_machine_arn)" \
  --name "manual-$(date +%s)"
```

Watch the execution in the console; the first run is where a missing S3
permission or a bad Postgres endpoint shows up. The SNS alert topic will fire
on failure.

### 7. Publish the dashboard

```bash
cd ../../web
npm ci
VITE_LIVE_URL="$(terraform output -raw live_api_url)" npm run build
aws s3 sync dist/ "s3://$(terraform output -raw web_bucket_name)/" --delete \
  --cache-control "public,max-age=31536000,immutable" --exclude "index.html" --exclude "*.json"
aws s3 cp dist/index.html "s3://$(terraform output -raw web_bucket_name)/index.html" \
  --cache-control "no-cache"
aws cloudfront create-invalidation \
  --distribution-id "$(terraform output -raw cloudfront_distribution_id)" --paths "/" "/index.html"
```

---

## Resource inventory

**Networking** — one VPC; three public, three private and three database
subnets across three AZs; one NAT gateway (per-AZ with
`single_nat_gateway = false`); an S3 Gateway endpoint; optional Interface
endpoints; six security groups with rules as separate resources.

**Compute** — one ECS Fargate cluster; the collector service (`desired_count`
from a variable, `awsvpc`, port 8090, `/health` checks at both container and
target-group level); the lake task definition (no service — the state machine
runs it).

**Event bus** — provisioned MSK with SASL/SCRAM over TLS on 9096, a broker
configuration, broker logs to CloudWatch, and SCRAM credentials in Secrets
Manager. Topics are created by the app, not Terraform.

**Lake** — one versioned, encrypted S3 bucket for Bronze/Silver/Gold; a Glue
catalog database and two crawlers; lifecycle to STANDARD_IA then Glacier IR.

**Warehouse** — Redshift Serverless namespace + workgroup (toggleable).

**Serving** — Aurora PostgreSQL Serverless v2 writer + reader, IAM database
authentication, RDS-managed master password, and a read-only role secret.

**Orchestration** — a Step Functions state machine that takes a DynamoDB lock,
runs the lake task with retry, and alerts SNS on failure; an EventBridge
Scheduler rule every 15 minutes.

**Edge** — an ALB for the live API; a private S3 bucket served through
CloudFront with an Origin Access Control and SPA error handling.

**Observability** — three log groups, one SNS topic, and alarms for collector
health, collector CPU, failed lake executions, MSK consumer lag, and lake data
freshness.

**CI** — a GitHub Actions OIDC provider and a deploy role trusted **only** for
`repo:<github_repository>:ref:refs/heads/<github_deploy_branch>` (main by
default), with ECR push, `ecs:UpdateService` on the collector, and
`states:StartExecution` on the lake machine.

---

## Things worth knowing

- **Single writer.** The lake state machine takes an atomic DynamoDB lock
  before running the task, because neither EventBridge Scheduler nor a Standard
  state machine offers concurrency control and DuckDB/Iceberg assume one writer
  per table. If an execution is force-stopped externally, the lock lingers;
  clear it with `aws dynamodb delete-item --table-name <prefix>-lake-lock
  --key '{"lock_id":{"S":"lake"}}'`.
- **Kafka auth is SCRAM, not IAM.** `kafka-python-ng` speaks SCRAM directly;
  IAM auth would need a signer plugin and a code change. If you later switch,
  change the mechanism in `locals`/`ecs.tf` and update the cluster's
  `client_authentication`.
- **CloudFront uses OAC, not OAI**, with a bucket policy scoped by
  `AWS:SourceArn`. The bucket is never public.
- **Aurora uses `engine_mode = "provisioned"`** — that is how Serverless v2
  works; `engine_mode = "serverless"` is the older v1 and does not apply here.
- **MSK consumer-lag metric.** `observability.tf` alarms on the `AWS/Kafka`
  `OffsetLag` metric with `Cluster Name` / `Consumer Group` / `Topic`
  dimensions, matching docs/aws-deployment.md §12. Confirm the exact metric and
  dimensions in your account's CloudWatch console before relying on it: the
  metric set differs between provisioned and Serverless MSK.
- **Custom lake freshness metric.** `observability.tf` alarms on
  `<lake_metrics_namespace>/<lake_freshness_metric_name>` with **no
  dimensions**. The writer is `scripts/publish_metrics.py`, which
  `scripts/run_lake.sh` runs at the end of every cycle when
  `LAKE_METRICS_ENABLED=1` (the lake task sets it). It emits the metric with
  `PutMetricData` and no dimensions, matching the alarm.
- **Redshift is provisioned but not yet wired into the task.** The warehouse
  credentials are in Secrets Manager and exported, but the lake image still
  talks to MotherDuck via `MOTHERDUCK_*`. Moving `dbt` to `dbt-redshift` is the
  application change the docs describe; Terraform is ready for it.
- **State is not remote by default.** The module works with local state out of
  the box. For a shared deployment, copy `backend.tf.example` to `backend.tf`
  and fill it in, or set the `TF_STATE_BUCKET` repository variable and let
  `.github/workflows/deploy-aws.yml` generate the S3 backend. The state contains
  generated passwords, so never commit it.

## Teardown

Order matters to avoid billable orphans and S3 errors:

```bash
# 1. Stop the collector and the schedule.
aws ecs update-service --cluster "$(terraform output -raw ecs_cluster_name)" \
  --service "$(terraform output -raw collector_service_name)" --desired-count 0
aws scheduler delete-schedule --name "$(terraform output -raw lake_schedule_name)"

# 2. Empty the buckets you are willing to lose (the web bucket at least).
#    The lake bucket is the only thing worth backing up.
aws s3 rm "s3://$(terraform output -raw web_bucket_name)" --recursive

# 3. Everything else.
terraform destroy
```

MSK deletion takes tens of minutes. Redshift Serverless, Aurora and CloudFront
all bill while they exist, so destroy promptly if you are only testing.
