# ─────────────────────────────────────────────────────────────────────────────
# Stages 3 + 4 — Catalog (Glue Crawler) and ETL / Transform (Glue PySpark job).
#
# The raw database is populated by the crawler; the curated database is written
# by the transform job. A conditional Glue trigger chains them: when the daily
# crawler run succeeds, the job runs. That is the idiomatic "after the crawler"
# pattern and it cannot double-fire the way two independent timers can.
# ─────────────────────────────────────────────────────────────────────────────

# ── Glue Data Catalog ────────────────────────────────────────────────────────

resource "aws_glue_catalog_database" "raw" {
  name        = local.raw_database_name
  description = "EU Air Traffic — raw/landing zone tables discovered from s3://${aws_s3_bucket.raw.bucket}/ (stage 2)"
}

resource "aws_glue_catalog_database" "curated" {
  name        = local.curated_database_name
  description = "EU Air Traffic — curated fact/dim/aggregate tables written by the transform job (stage 5)"
}

# ── Stage 3: crawler ─────────────────────────────────────────────────────────
# Scans the whole raw bucket. The five domain prefixes share a Parquet shape but
# a crawler target per prefix would mean five crawlers to keep in step; one
# target plus `CombineCompatibleSchemas` is simpler and still partitions by path.

resource "aws_glue_crawler" "raw" {
  name          = local.crawler_name
  description   = "Discover raw Parquet under s3://${aws_s3_bucket.raw.bucket}/"
  role          = aws_iam_role.glue_crawler.arn
  database_name = aws_glue_catalog_database.raw.name
  table_prefix  = var.crawler_table_prefix
  schedule      = var.crawler_schedule

  s3_target {
    path = "s3://${aws_s3_bucket.raw.bucket}/"
  }

  # New columns are added in place; a column that disappears is logged rather
  # than silently dropped. This is the safe default for an append-only zone.
  schema_change_policy {
    update_behavior = "UPDATE_IN_DATABASE"
    delete_behavior = "LOG"
  }

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = {
        AddOrUpdateBehavior = "InheritFromTable"
      }
    }
    Grouping = {
      TableGroupingPolicy = "CombineCompatibleSchemas"
    }
  })

  tags = {
    Name  = local.crawler_name
    Stage = "3-catalog"
  }
}

# ── Stage 4: ETL script artifact ─────────────────────────────────────────────
# The PySpark source is owned by the application repo. Terraform uploads it and
# feeds the resulting S3 URI to the job so the job and the repo cannot drift.

resource "aws_s3_object" "etl_script" {
  bucket = aws_s3_bucket.artifacts.id
  key    = local.etl_script_key
  source = local.etl_script_local_path

  # Re-upload whenever the checked-in script changes.
  source_hash  = filemd5(local.etl_script_local_path)
  content_type = "text/x-python"

  tags = {
    Name  = "glue-etl-script"
    Stage = "4-transform"
  }
}

# ── Stage 4: ETL / transform job ─────────────────────────────────────────────

resource "aws_glue_job" "transform" {
  name        = local.glue_job_name
  description = "Transform raw ADS-B/OpenSky/AirLabs/METAR/Eurostat data into curated fact/dim/aggregate Parquet"
  role_arn    = aws_iam_role.glue_job.arn

  glue_version      = var.glue_version
  worker_type       = var.glue_job_worker_type
  number_of_workers = var.glue_job_number_of_workers
  timeout           = var.glue_job_timeout_minutes
  max_retries       = var.glue_job_max_retries

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.artifacts.bucket}/${local.etl_script_key}"
    python_version  = "3"
  }

  execution_property {
    # One writer to the curated zone. A second concurrent run would race the
    # job's partition writes.
    max_concurrent_runs = 1
  }

  default_arguments = {
    "--RAW_DATABASE"     = aws_glue_catalog_database.raw.name
    "--CURATED_DATABASE" = aws_glue_catalog_database.curated.name
    "--CURATED_BUCKET"   = aws_s3_bucket.curated.bucket
    "--CURATED_PREFIX"   = var.curated_prefix

    # Shuffle/spill scratch space. Kept in the artifacts bucket so curated
    # storage stays pure data.
    "--TempDir" = "s3://${aws_s3_bucket.artifacts.bucket}/tmp/"

    "--job-language"                     = "python"
    "--enable-continuous-cloudwatch-log" = "true"
    "--continuous-log-logGroup"          = aws_cloudwatch_log_group.glue_job.name

    # Publish driver/executor metrics (glue.driver.aggregate.*) so the failure
    # alarm below has something to read. Per the Glue docs, --enable-metrics is
    # a presence-based flag: include it with an empty value, not "true".
    "--enable-metrics"      = ""
    "--enable-job-insights" = "true"
  }

  # The job must not start before its script object exists in S3.
  depends_on = [aws_s3_object.etl_script]

  tags = {
    Name  = local.glue_job_name
    Stage = "4-transform"
  }
}

# ── Stage 4: chain the job behind the crawler ────────────────────────────────
# A CONDITIONAL trigger watches the crawler and runs the job when the crawl
# SUCCEEDS. The crawler's own `schedule` is the clock; this trigger is the link.
#
# If you would rather drive the job purely on a timer (for example, when the
# crawler is replaced by explicit table registration), delete this resource and
# use the SCHEDULED trigger sketch in the comment below instead.
#
# resource "aws_glue_trigger" "scheduled_transform" {
#   name     = "${local.name_prefix}-scheduled-transform"
#   type     = "SCHEDULED"
#   schedule = "cron(30 3 * * ? *)"   # 30 minutes after the crawler
#   actions {
#     job_name = aws_glue_job.transform.name
#   }
# }

resource "aws_glue_trigger" "crawler_to_job" {
  name              = local.glue_trigger_name
  description       = "Run the transform job after a successful raw-zone crawl"
  type              = "CONDITIONAL"
  start_on_creation = true

  predicate {
    conditions {
      crawler_name = aws_glue_crawler.raw.name
      crawl_state  = "SUCCEEDED"
    }
  }

  actions {
    job_name = aws_glue_job.transform.name
  }

  tags = {
    Name  = local.glue_trigger_name
    Stage = "4-transform"
  }
}
