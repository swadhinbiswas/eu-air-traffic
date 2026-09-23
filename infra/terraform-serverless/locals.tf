# ─────────────────────────────────────────────────────────────────────────────
# Account/region lookups, naming, tags, and the shared ARNs that the IAM
# policies reuse. Derived values live here so a name is defined exactly once.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  name_prefix = var.name_prefix
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.name

  # var.tags is the operator escape hatch; the managed tags are not overridable
  # so cost allocation stays consistent. `Stack` is what separates this module's
  # spend from the managed stack in the same account.
  tags = merge(
    var.tags,
    {
      Project     = "eu-air-traffic"
      Environment = var.environment
      ManagedBy   = "terraform"
      Stack       = "serverless-analytics"
    },
  )

  # ── Storage bucket names ───────────────────────────────────────────────────
  # S3 names are global. Namespacing with the account id lets this module run in
  # more than one account without a collision; an explicit name still wins.
  raw_bucket_name       = var.raw_bucket_name != "" ? var.raw_bucket_name : "${local.name_prefix}-raw-${local.account_id}"
  curated_bucket_name   = var.curated_bucket_name != "" ? var.curated_bucket_name : "${local.name_prefix}-curated-${local.account_id}"
  artifacts_bucket_name = var.artifacts_bucket_name != "" ? var.artifacts_bucket_name : "${local.name_prefix}-artifacts-${local.account_id}"

  # ── The upstream (managed-stack) lake bucket ───────────────────────────────
  # Stage 1 reads from the Bronze/Silver DuckDB warehouse object store that the
  # managed stack already owns. Default it to that stack's conventional name so
  # a same-account deployment needs no wiring; override for a cross-account or
  # renamed bucket.
  source_bucket = var.source_bucket != "" ? var.source_bucket : "${local.name_prefix}-lake-${local.account_id}"

  # The five Kafka-fed domains from the on-prem pipeline. The map drives both
  # the Lambda environment variables and the raw-zone lifecycle tiering rules.
  raw_prefixes = {
    adsb     = "adsb/"
    opensky  = "opensky/"
    airlabs  = "airlabs/"
    metar    = "metar/"
    eurostat = "eurostat/"
  }

  # ── Derived resource names ─────────────────────────────────────────────────
  lambda_name           = var.lambda_function_name != "" ? var.lambda_function_name : "${local.name_prefix}-bronze-export"
  raw_database_name     = var.raw_database_name != "" ? var.raw_database_name : "${local.name_prefix}_raw"
  curated_database_name = var.curated_database_name != "" ? var.curated_database_name : "${local.name_prefix}_curated"

  crawler_name      = var.crawler_name != "" ? var.crawler_name : "${local.name_prefix}-raw-crawler"
  glue_job_name     = var.glue_job_name != "" ? var.glue_job_name : "${local.name_prefix}-transform"
  glue_trigger_name = var.glue_trigger_name != "" ? var.glue_trigger_name : "${local.name_prefix}-crawler-to-transform"
  etl_script_key    = var.glue_script_key != "" ? var.glue_script_key : "glue-scripts/etl_job.py"

  athena_workgroup_name   = var.athena_workgroup_name != "" ? var.athena_workgroup_name : "${local.name_prefix}-analytics"
  athena_results_location = "s3://${local.artifacts_bucket_name}/${var.athena_results_prefix}"

  # ── Repository-side source locations ───────────────────────────────────────
  # Both files live in the application repo and are created by the app team, not
  # by Terraform. If either is missing, `plan`/`apply` fail; `validate` does not
  # read them. See README "Prerequisites".
  lambda_source_dir     = "${path.module}/../../aws/lambda/bronze_export"
  etl_script_local_path = "${path.module}/../../aws/glue/etl_job.py"

  # ── Glue Data Catalog ARNs ─────────────────────────────────────────────────
  # Reused by every Glue IAM policy so the two databases are the only ones any
  # role in this stack can read or write.
  glue_catalog_arn          = "arn:aws:glue:${local.region}:${local.account_id}:catalog"
  glue_raw_database_arn     = "arn:aws:glue:${local.region}:${local.account_id}:database/${local.raw_database_name}"
  glue_raw_table_arn        = "arn:aws:glue:${local.region}:${local.account_id}:table/${local.raw_database_name}/*"
  glue_curated_database_arn = "arn:aws:glue:${local.region}:${local.account_id}:database/${local.curated_database_name}"
  glue_curated_table_arn    = "arn:aws:glue:${local.region}:${local.account_id}:table/${local.curated_database_name}/*"

  # ── Lake-zone ARNs ─────────────────────────────────────────────────────────
  raw_bucket_arn       = "arn:aws:s3:::${local.raw_bucket_name}"
  curated_bucket_arn   = "arn:aws:s3:::${local.curated_bucket_name}"
  artifacts_bucket_arn = "arn:aws:s3:::${local.artifacts_bucket_name}"
  source_bucket_arn    = "arn:aws:s3:::${local.source_bucket}"

  # ── Ready-to-paste Athena snippet (also exposed as an output) ──────────────
  # Deliberately generic: the curated table names are owned by ../../aws/glue/
  # etl_job.py, so discovery happens before guessing. Athena runs one statement
  # per execution, so the discovery query is kept separate from the example
  # aggregate that follows it in the output.
  athena_discovery_query = join(" ", [
    "SELECT table_name, table_type",
    "FROM information_schema.tables",
    "WHERE table_schema = '${local.curated_database_name}'",
    "ORDER BY table_name",
  ])

  athena_example_query = <<-EOT
    -- 1) Discover what the crawler / transform job registered.
    ${local.athena_discovery_query};

    -- 2) Then point a query at one of them (replace the table name):
    -- SELECT * FROM "${local.curated_database_name}"."fact" LIMIT 100;
  EOT
}
