# EU Air Traffic — Terraform (serverless analytics stack)

This root module provisions the **S3 + Glue + Athena** half of the
`eu-air-traffic` architecture — the path an on-prem/free Kafka + DuckDB pipeline
takes once it reaches AWS, shown in
[`../../images/aws-version.png`](../../images/aws-version.png) and described in
[`../../docs/aws-version.md`](../../docs/aws-version.md).

It is intentionally a **separate root module** from
[`../terraform/`](../terraform/) (the always-on managed stack: VPC, MSK, ECS,
Aurora, Redshift, CloudFront). The two share no state and may share the same
source bucket. This one creates no networking: every resource is a regional,
pay-per-use service.

> **Reference implementation — NOT currently deployed.** Nothing in this
> directory is provisioned in any account. The live pipeline still runs on free
> tiers. This module exists to show the serverless scale path as reviewable
> code, and to be a safe starting point when the free tier stops being enough.

---

## The seven stages

The diagram is left to right. Each stage maps to a resource below.

| # | Stage | Service | Resource / file |
|---|---|---|---|
| 1 | Export raw data | **AWS Lambda** ("Bronze-layer export job") | `aws_lambda_function.bronze_export` + `aws_scheduler_schedule.bronze_export` — `lambda.tf` |
| 2 | Raw data storage | **Amazon S3** — raw/landing zone | `aws_s3_bucket.raw` — `s3.tf` |
| 3 | Catalog | **AWS Glue Crawler** | `aws_glue_crawler.raw`, `aws_glue_catalog_database.raw` — `glue.tf` |
| 4 | ETL / transform | **AWS Glue ETL job** (PySpark) | `aws_glue_job.transform`, `aws_glue_trigger.crawler_to_job` — `glue.tf` |
| 5 | Curated data storage | **Amazon S3** — curated zone | `aws_s3_bucket.curated`, `aws_glue_catalog_database.curated` — `s3.tf` |
| 6 | Query | **Amazon Athena** | `aws_athena_workgroup.analytics`, `aws_athena_named_query.sample` — `athena.tf` |
| 7 | Analytics / BI | QuickSight / Grafana / other | *not provisioned* — point it at the workgroup |
| — | Artifacts | S3 (Glue script, `--TempDir`, Athena results) | `aws_s3_bucket.artifacts` — `s3.tf` |
| — | IAM | One least-privilege role per actor | `iam.tf` |
| — | Observability | Log groups, alarms, SNS topic | `observability.tf` |

### Upstream (not provisioned here)

```
Kafka event bus (5 topics) ──> Bronze/Silver DuckDB warehouse
   ADS-B · OpenSky · AirLabs · METAR/TAF · Eurostat
```

Stage 1 reads that warehouse's object store (the managed stack's lake bucket)
and lands Parquet in this stack's raw bucket. The upstream bucket is **not**
created here; it is referenced read-only through `var.source_bucket` (default
`<name_prefix>-lake-<account_id>`).

### Data flow through the buckets

```
source_bucket ──[1 Lambda, hourly]──> raw_bucket            ──[3 crawler, daily]──> Glue raw DB
                                       adsb/ opensky/                                 (stage 3)
                                       airlabs/ metar/
                                       eurostat/
                                                   │
                              [4 Glue PySpark job, chained after a successful crawl]
                                                   ▼
                                       curated_bucket       ──[5]──> Glue curated DB
                                       fact/ dim/                       │
                                       aggregates/ parquet/             ▼
                                                          [6 Athena workgroup, enforced]
                                                                        │
                                                                        ▼
                                                          [7 QuickSight / Grafana]
```

Raw is append-only; curated is derived and can always be rebuilt from raw. The
artifacts bucket keeps the ETL script and query results out of both data zones.

---

## Files

| File | Owns |
|---|---|
| `versions.tf` | Terraform/provider pins (`>= 1.10.0`, `aws ~> 5.60`, `archive ~> 2.4`) |
| `providers.tf` | AWS provider + `default_tags` |
| `variables.tf` | Every input, with defaults and validations |
| `locals.tf` | Naming, tags, catalog/bucket ARNs, the example query |
| `s3.tf` | Raw, curated and artifacts buckets + lifecycle |
| `lambda.tf` | Stage 1: export function, zip, scheduler, manual-invoke path |
| `glue.tf` | Stages 3–4: databases, crawler, script upload, ETL job, trigger |
| `athena.tf` | Stage 6: workgroup and named example query |
| `iam.tf` | Four least-privilege roles (Lambda, Scheduler, crawler, Glue job) |
| `observability.tf` | Log groups, SNS topic/policy, three alarms, Glue event rule |
| `outputs.tf` | Bucket/Lambda/crawler/job/database/workgroup outputs + example SQL |
| `terraform.tfvars.example` | Documented, copy-to-start variables |

---

## Prerequisites

- **Terraform** >= 1.10 (developed against 1.10.1). The S3 backend guidance
  below uses 1.10 native locking (`use_lockfile`), so do not downgrade.
- **AWS credentials** with permission to create S3, Lambda, IAM, Glue, Athena,
  EventBridge Scheduler, CloudWatch, SNS and EventBridge resources. `aws
  configure` or `AWS_PROFILE` is enough.
- **The application source**, checked in at two repo paths this module reads:
  - `aws/lambda/bronze_export/` — the export handler (`handler.py`, entry point
    `handler.lambda_handler`). Zipped at plan time by `data "archive_file"`.
  - `aws/glue/etl_job.py` — the PySpark transform. Uploaded to the artifacts
    bucket by `aws_s3_object`.

  `terraform validate` does not read either file, but `terraform plan` **fails**
  if they are missing.
- **A Lambda layer providing `pyarrow`** (and `huggingface_hub` on the Hugging
  Face source path). Neither ships in the `python3.12` runtime, so the first
  invoke fails at import until `lambda_layer_arns` points at a layer that has
  them. See [`aws/README.md`](../../aws/README.md); leave it empty only if you
  run the function from a container image that already bundles them.
- No networking is required: there is no VPC, NAT or security group. If the
  upstream bucket is in another account, add an S3 bucket policy there allowing
  this stack's Lambda role to read it.

State is intentionally not configured, so `terraform init` uses local state.
Before a real deployment, add an `s3` backend with locking and never commit
`terraform.tfstate` — it contains resource identifiers and ARNs. The managed
stack's [`../terraform/backend.tf.example`](../terraform/backend.tf.example) is
a ready-made template for the same account.

---

## Usage

```bash
cd infra/terraform-serverless

cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars            # at minimum: set alarm_email, maybe lambda_layer_arns

terraform init                      # add -backend=false for a local-only test run
terraform fmt -check -recursive
terraform validate
terraform plan  -out tfplan
terraform apply tfplan
```

Bucket names default to `<name_prefix>-<zone>-<account_id>`, which is already
globally unique. Override them only to reuse an existing name.

---

## Post-apply steps

Terraform creates the pipeline but does not run it. In order:

1. **Run the export once.** The scheduler calls it hourly, but run it now to
   prove the path works:
   ```bash
   aws lambda invoke --function-name "$(terraform output -raw lambda_function_name)" \
     --payload '{}' /tmp/bronze-export.json && cat /tmp/bronze-export.json
   aws s3 ls "s3://$(terraform output -raw raw_bucket_name)/" --recursive | head
   ```

2. **Run the crawler** and wait for it:
   ```bash
   aws glue start-crawler --name "$(terraform output -raw glue_crawler_name)"
   aws glue get-crawler  --name "$(terraform output -raw glue_crawler_name)" \
     --query 'Crawler.State'
   ```
   It also runs daily on `crawler_schedule`. When a run succeeds, the
   conditional Glue trigger runs the transform job automatically.

3. **Run the transform job** (if you do not want to wait for a crawl):
   ```bash
   aws glue start-job-run --job-name "$(terraform output -raw glue_job_name)"
   aws glue get-job-runs --job-name "$(terraform output -raw glue_job_name)" \
     --query 'JobRuns[0].[JobRunState,ErrorMessage]'
   ```

4. **Open Athena.** Select the workgroup from `athena_workgroup_name`, the
   database from `athena_database_name`, and paste `athena_example_query`:
   ```bash
   terraform output -raw athena_example_query
   ```
   The first statement lists the curated tables; the second, commented out, is
   where you point a real query.

5. **Connect BI (stage 7).** QuickSight: create a data source of type Athena,
   choose the workgroup and database, and the curated tables appear. Grafana:
   the `athena` datasource plugin with the same workgroup/database. Both honour
   the workgroup's enforced scan limit and result location.

---

## Manual verification checklist

- [ ] `terraform validate` reports **Success**.
- [ ] `terraform plan` shows creates only — it must not touch the managed stack
      (this module shares no state with it).
- [ ] All three buckets are private:
      `aws s3api get-public-access-block --bucket <name>` shows four `true`s.
- [ ] `aws s3api get-bucket-ownership-controls --bucket <name>` reports `BucketOwnerEnforced`.
- [ ] `aws s3api get-bucket-encryption --bucket <name>` reports `AES256`.
- [ ] Log group `/aws/lambda/<prefix>-bronze-export` shows a recent
      `START`/`END`/`REPORT`, and the function returned no error.
- [ ] The raw bucket has data under the five domain prefixes
      (`adsb/`, `opensky/`, `airlabs/`, `metar/`, `eurostat/`).
- [ ] The crawler is `READY` and the raw database has tables:
      `aws glue get-tables --database-name "$(terraform output -raw glue_raw_database_name)"`.
- [ ] The Glue job's last run is `SUCCEEDED`; curated `fact/`, `dim/` and
      `aggregates/` prefixes exist in the curated bucket.
- [ ] An Athena `SELECT ... LIMIT 10` against a curated table succeeds and the
      workgroup reports a small "data scanned" (partition pruning working).
- [ ] `aws scheduler get-schedule --name <prefix>-bronze-export --group-name default`
      shows the expected expression.
- [ ] The alert topic subscription is confirmed (check the `alarm_email` inbox).
- [ ] Force one failure (e.g. temporarily rename the Lambda handler), confirm the
      alarm and the Glue-event notification arrive, then revert.

---

## Signals and what to trust

| Alarm | What it watches | Confidence |
|---|---|---|
| `...-bronze-export-errors` | `AWS/Lambda Errors` for the export function | High — standard metric |
| `...-glue-job-state` (EventBridge → SNS) | Glue `Job State Change` at `FAILED`/`TIMEOUT`/`STOPPED` | **Highest** — every run emits it |
| `...-transform-failed-tasks` | `Glue` / `glue.driver.aggregate.numFailedTasks` (`{JobName, JobRunId=ALL, Type}`) | Medium — a script error can fail a run with zero failed *tasks*; confirm the exact dimensions for your Glue version in the console |
| `...-raw-data-stale` | Custom `RawDataAgeSeconds` metric | **Skeleton** — nothing publishes it yet; see `observability.tf` for the one-line `put_metric_data` call that brings it to life |

---

## Cost

The only always-on charge is S3 storage; everything else is pay-per-use.

- **S3** — the dominant line item. Raw partitions move to `STANDARD_IA` after
  `raw_ia_transition_days` (30) and `GLACIER_IR` after
  `raw_glacier_transition_days` (90); noncurrent versions expire after
  `noncurrent_version_expiration_days` (90). Curated data is **not** tiered,
  because Athena cannot query archived objects. Abandoned multipart uploads are
  aborted after `abort_incomplete_multipart_upload_days` (7). Typically a few
  dollars a month.
- **Lambda** — 512 MB × ≤15 min hourly is a rounding error, especially on
  Graviton (arm64).
- **Glue** — billed in **DPU-hours**: `2 × G.1X` workers for the job's runtime,
  plus the crawler's crawl time (crawlers bill a 10-minute minimum per crawl).
  This is the largest *consumption* item after storage; reduce
  `glue_job_number_of_workers` first if the transform is small.
- **Athena** — pay-per-query on bytes scanned (~$5/TB in most regions). The
  workgroup enforces `athena_bytes_scanned_cutoff_per_query` (default 10 GiB) so
  a stray `SELECT *` cannot run away. Partitioned Parquet plus `WHERE` on
  partition columns keeps scans small.
- **QuickSight** is the one fixed monthly cost (per author) if you use it.
- **CloudWatch Logs, SNS, EventBridge Scheduler** — effectively free at this
  volume; log groups expire after `log_retention_days` (30).

To drop everything but S3 storage to zero, set `lambda_schedule_enabled = false`
and remove the crawler's schedule.

---

## Teardown

```bash
aws scheduler delete-schedule --name "$(terraform output -raw lambda_schedule_name)" 2>/dev/null || true
terraform destroy
```

The buckets are versioned and created with `force_destroy = false`, so a
non-empty bucket makes `destroy` fail rather than silently delete data. Set
`raw_bucket_force_destroy` / `curated_bucket_force_destroy` /
`artifacts_bucket_force_destroy` to `true` only in a throwaway sandbox. Raw and
curated data is derived, so nothing here is irreplaceable.

---

## Notes and deliberate choices

- **Why three buckets.** Raw and curated have different access and lifecycle
  needs; the artifacts bucket keeps the Glue script and Athena results out of
  both, so a lifecycle rule on data never touches a script.
- **Why a CONDITIONAL Glue trigger.** It links stages 3 and 4 without a second
  timer: the crawler's schedule is the clock, and the job runs only when the
  crawl succeeded. A commented `SCHEDULED` alternative sits beside it in
  `glue.tf`.
- **Why the workgroup is enforced.** `enforce_workgroup_configuration = true`
  stops a client redirecting results to its own bucket or lifting the scan
  limit, which is what makes the workgroup safe to hand to a dashboard team.
- **Why names embed the account id.** Every default name includes the caller's
  account id, so the module is safe in any account without a rename.
- **Tags.** Everything taggable carries `Project`, `Environment`, `ManagedBy`
  and `Stack = "serverless-analytics"`, separating this module's spend from the
  managed stack in Cost Explorer.

## Known uncertainties

A few provider details are worth a second look on first apply:

- `aws_scheduler_schedule` has **no `tags` argument** in the 5.x provider, so
  the schedule is identified by name only.
- The Glue metrics alarm assumes the dimension set `{JobName, JobRunId, Type}`.
  The `--enable-metrics` job parameter is presence-based and is passed as an
  empty string for that reason. If the alarm sits at `INSUFFICIENT_DATA`, check
  the exact dimensions in the CloudWatch console — the EventBridge rule is the
  reliable failure signal regardless.
- The freshness alarm has no producer yet; it is an explicit skeleton.
