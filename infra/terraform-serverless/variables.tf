# ─────────────────────────────────────────────────────────────────────────────
# Inputs for the serverless analytics stack.
#
# Defaults are chosen so a same-account `terraform apply` produces a complete,
# working pipeline without any required inputs. The values that genuinely differ
# per deployment (an alarm email, an override bucket name) have safe defaults
# and are all documented again in terraform.tfvars.example.
# ─────────────────────────────────────────────────────────────────────────────

# ── Identity / tagging ───────────────────────────────────────────────────────

variable "name_prefix" {
  description = "Prefix for every resource name in this stack. Keep it short and S3-safe; it also names the default buckets and Glue databases."
  type        = string
  default     = "eu-air-traffic"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 3-32 lowercase alphanumeric/hyphen characters, starting and ending with a letter or digit."
  }
}

variable "environment" {
  description = "Deployment environment, used in every resource tag."
  type        = string
  default     = "production"

  validation {
    condition     = contains(["development", "test", "production"], var.environment)
    error_message = "environment must be one of: development, test, production."
  }
}

variable "aws_region" {
  description = "AWS region for S3, Lambda, Glue and Athena. Pick the region closest to the upstream data."
  type        = string
  default     = "eu-central-1"
}

variable "tags" {
  description = "Extra tags merged into every resource alongside Project/Environment/ManagedBy/Stack."
  type        = map(string)
  default     = {}
}

# ── Storage: raw (landing) zone ──────────────────────────────────────────────

variable "raw_bucket_name" {
  description = "Override the raw/landing bucket name. Empty derives <name_prefix>-raw-<account_id>."
  type        = string
  default     = ""
}

variable "raw_bucket_force_destroy" {
  description = "Allow Terraform to delete the raw bucket even when it holds objects. Leave false outside ephemeral sandboxes."
  type        = bool
  default     = false
}

variable "raw_ia_transition_days" {
  description = "Days before raw partitions move to STANDARD_IA (from their creation date)."
  type        = number
  default     = 30

  validation {
    condition     = var.raw_ia_transition_days >= 0
    error_message = "raw_ia_transition_days must be zero or greater."
  }
}

variable "raw_glacier_transition_days" {
  description = "Days before raw partitions move to GLACIER_IR. Must be greater than raw_ia_transition_days."
  type        = number
  default     = 90

  validation {
    condition     = var.raw_glacier_transition_days > var.raw_ia_transition_days
    error_message = "raw_glacier_transition_days must be greater than raw_ia_transition_days."
  }
}

# ── Storage: curated zone ────────────────────────────────────────────────────

variable "curated_bucket_name" {
  description = "Override the curated bucket name. Empty derives <name_prefix>-curated-<account_id>."
  type        = string
  default     = ""
}

variable "curated_bucket_force_destroy" {
  description = "Allow Terraform to delete the curated bucket even when it holds objects. Leave false outside ephemeral sandboxes."
  type        = bool
  default     = false
}

# ── Storage: artifacts (Glue script + Athena results) ────────────────────────

variable "artifacts_bucket_name" {
  description = "Override the artifacts bucket name (Glue scripts + Athena query results). Empty derives <name_prefix>-artifacts-<account_id>."
  type        = string
  default     = ""
}

variable "artifacts_bucket_force_destroy" {
  description = "Allow Terraform to delete the artifacts bucket even when it holds objects. Leave false outside ephemeral sandboxes."
  type        = bool
  default     = false
}

variable "artifacts_expiration_days" {
  description = "Days before objects under the Athena results prefix are expired. Query results are regenerable."
  type        = number
  default     = 30

  validation {
    condition     = var.artifacts_expiration_days >= 1
    error_message = "artifacts_expiration_days must be at least 1."
  }
}

variable "noncurrent_version_expiration_days" {
  description = "Days before a noncurrent object version in any bucket is permanently deleted."
  type        = number
  default     = 90

  validation {
    condition     = var.noncurrent_version_expiration_days >= 1
    error_message = "noncurrent_version_expiration_days must be at least 1."
  }
}

variable "abort_incomplete_multipart_upload_days" {
  description = "Days after initiation before an incomplete multipart upload is aborted and its parts reclaimed."
  type        = number
  default     = 7

  validation {
    condition     = var.abort_incomplete_multipart_upload_days >= 1
    error_message = "abort_incomplete_multipart_upload_days must be at least 1."
  }
}

# ── Stage 1: Bronze export Lambda ────────────────────────────────────────────

variable "source_bucket" {
  description = "Existing managed-stack lake bucket the export job reads from. Empty derives <name_prefix>-lake-<account_id>."
  type        = string
  default     = ""
}

variable "lambda_function_name" {
  description = "Override the export Lambda name. Empty derives <name_prefix>-bronze-export."
  type        = string
  default     = ""
}

variable "lambda_memory_mb" {
  description = "Memory (MB) for the export Lambda. More memory also buys proportionally more CPU; Parquet export is CPU-bound."
  type        = number
  default     = 512
}

variable "lambda_timeout_seconds" {
  description = "Timeout (seconds) for the export Lambda. Lambda's hard maximum is 900."
  type        = number
  default     = 900

  validation {
    condition     = var.lambda_timeout_seconds >= 1 && var.lambda_timeout_seconds <= 900
    error_message = "lambda_timeout_seconds must be between 1 and 900."
  }
}

variable "lambda_schedule_expression" {
  description = "EventBridge Scheduler expression for the export job. `rate(1 hour)` by default; also accepts cron(...)."
  type        = string
  default     = "rate(1 hour)"
}

variable "lambda_schedule_enabled" {
  description = "Create (and enable) the EventBridge Scheduler schedule. Set false to deploy the function without a timer; it can still be invoked manually."
  type        = bool
  default     = true
}

variable "lambda_log_level" {
  description = "LOG_LEVEL passed to the export handler."
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR"], var.lambda_log_level)
    error_message = "lambda_log_level must be one of: DEBUG, INFO, WARNING, ERROR."
  }
}

variable "lambda_layer_arns" {
  description = <<-EOT
    Lambda layer ARNs to attach to the export function. The handler imports
    pyarrow (to write Parquet) and, on the Hugging Face path, huggingface_hub —
    neither ships with the python3.12 runtime. Build or reuse a layer that
    provides them (see aws/README.md) and list it here; leave empty only if you
    run the function from a container image that already contains them.
  EOT
  type        = list(string)
  default     = []
}

# ── Stage 3-4: Glue catalog, crawler and ETL job ─────────────────────────────

variable "raw_database_name" {
  description = "Glue Data Catalog database for crawled raw tables. Empty derives <name_prefix>_raw."
  type        = string
  default     = ""
}

variable "curated_database_name" {
  description = "Glue Data Catalog database for curated tables. Empty derives <name_prefix>_curated."
  type        = string
  default     = ""
}

variable "crawler_name" {
  description = "Override the raw-zone crawler name. Empty derives <name_prefix>-raw-crawler."
  type        = string
  default     = ""
}

variable "crawler_schedule" {
  description = "Cron expression for the daily raw-zone crawl, in UTC. Default 03:00 daily."
  type        = string
  default     = "cron(0 3 * * ? *)"
}

variable "crawler_table_prefix" {
  description = "Optional prefix applied to every table the crawler creates. Empty keeps the table name derived from the S3 path."
  type        = string
  default     = ""
}

variable "glue_job_name" {
  description = "Override the Glue ETL job name. Empty derives <name_prefix>-transform."
  type        = string
  default     = ""
}

variable "glue_script_key" {
  description = "S3 key the ETL script is uploaded to in the artifacts bucket. Empty derives glue-scripts/etl_job.py."
  type        = string
  default     = ""
}

variable "glue_version" {
  description = "AWS Glue version for the ETL job. Glue 4.0 runs Python 3.10 / Spark 3.3 on Graviton-capable workers."
  type        = string
  default     = "4.0"
}

variable "glue_job_worker_type" {
  description = "Glue worker type: G.1X (1 DPU), G.2X (2 DPU) or G.025X for very small jobs."
  type        = string
  default     = "G.1X"

  validation {
    condition     = contains(["G.025X", "G.1X", "G.2X"], var.glue_job_worker_type)
    error_message = "glue_job_worker_type must be one of: G.025X, G.1X, G.2X."
  }
}

variable "glue_job_number_of_workers" {
  description = "Number of Glue workers. 2 is the minimum for G.1X and enough for small Parquet transforms."
  type        = number
  default     = 2

  validation {
    condition     = var.glue_job_number_of_workers >= 2
    error_message = "glue_job_number_of_workers must be at least 2."
  }
}

variable "glue_job_timeout_minutes" {
  description = "Glue job timeout in minutes."
  type        = number
  default     = 60
}

variable "glue_job_max_retries" {
  description = "Times Glue retries a failed run before giving up. Keep it low: every retry is billed DPU-hours."
  type        = number
  default     = 1
}

variable "curated_prefix" {
  description = "Prefix the ETL job writes curated output under. Empty means the curated bucket root; the job appends fact/, dim/, aggregates/ and parquet/ itself."
  type        = string
  default     = ""
}

variable "glue_trigger_name" {
  description = "Override the crawler-to-job trigger name. Empty derives <name_prefix>-crawler-to-transform."
  type        = string
  default     = ""
}

# ── Stage 6: Athena ──────────────────────────────────────────────────────────

variable "athena_workgroup_name" {
  description = "Override the Athena workgroup name BI tools connect to. Empty derives <name_prefix>-analytics."
  type        = string
  default     = ""
}

variable "athena_bytes_scanned_cutoff_per_query" {
  description = "Per-query scan limit enforced by the workgroup, in bytes. Default 10 GiB; this is the guard-rail against a runaway SELECT *."
  type        = number
  default     = 10737418240
}

variable "athena_results_prefix" {
  description = "Prefix under the artifacts bucket where Athena writes query results."
  type        = string
  default     = "athena-results/"
}

# ── Observability ────────────────────────────────────────────────────────────

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the Lambda and Glue log groups."
  type        = number
  default     = 30
}

variable "alarm_email" {
  description = "Email address subscribed to the alert topic. Empty creates the topic but no subscription."
  type        = string
  default     = ""
}

variable "lambda_error_threshold" {
  description = "Number of Lambda errors in one 5-minute period that trips the alarm."
  type        = number
  default     = 1
}

variable "glue_failed_tasks_threshold" {
  description = "Failed Glue tasks in one 5-minute period that trips the metrics alarm."
  type        = number
  default     = 1
}

variable "enable_glue_event_alarm" {
  description = "Create the EventBridge rule that pages on any Glue job reaching FAILED/TIMEOUT/STOPPED. This is the reliable failure signal; the metric alarm is best-effort."
  type        = bool
  default     = true
}

variable "stale_data_metric_namespace" {
  description = "CloudWatch namespace for the custom raw-data freshness metric."
  type        = string
  default     = "EUAirTraffic/Serverless"
}

variable "stale_data_metric_name" {
  description = "Custom metric the export job should publish: age in seconds of the newest raw object."
  type        = string
  default     = "RawDataAgeSeconds"
}

variable "stale_data_threshold_seconds" {
  description = "Age in seconds past which raw data is considered stale. Default 7200 (two hours), twice the default hourly export cadence."
  type        = number
  default     = 7200
}

variable "enable_stale_data_alarm" {
  description = "Create the freshness alarm skeleton. It stays INSUFFICIENT_DATA until the export job starts publishing the custom metric."
  type        = bool
  default     = true
}
