# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Export raw data (AWS Lambda, "Bronze-layer export job").
#
# The handler lives in the application repo at aws/lambda/bronze_export/. It
# exports the upstream lake's historical objects as Parquet into the raw S3
# zone, one prefix per Kafka-fed domain. It is invoked on a schedule and can
# also be invoked by hand (`aws lambda invoke`), which is why no resource policy
# is needed for the manual path — an operator's own IAM permissions are enough.
# ─────────────────────────────────────────────────────────────────────────────

# Zips ../../aws/lambda/bronze_export/ at plan time. The provider creates
# build/ if it does not exist; .gitignore keeps the zip out of git.
data "archive_file" "bronze_export" {
  type        = "zip"
  source_dir  = local.lambda_source_dir
  output_path = "${path.module}/build/bronze_export.zip"
}

resource "aws_lambda_function" "bronze_export" {
  function_name = local.lambda_name
  description   = "Bronze-layer export job: land upstream data as Parquet in the raw S3 zone"
  role          = aws_iam_role.lambda.arn

  filename         = data.archive_file.bronze_export.output_path
  source_code_hash = data.archive_file.bronze_export.output_base64sha256

  # Handler, runtime and architecture are fixed to match the app team's source:
  # Python 3.12 on Graviton (arm64), which is cheaper per ms than x86_64.
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  architectures = ["arm64"]

  memory_size = var.lambda_memory_mb
  timeout     = var.lambda_timeout_seconds

  # pyarrow is not in the python3.12 runtime; supply it via a layer (or a
  # container image). Empty by default so a plain `apply` still plans, but the
  # first invoke will fail at import until a layer is attached — see
  # aws/README.md.
  layers = var.lambda_layer_arns

  environment {
    variables = {
      RAW_BUCKET = aws_s3_bucket.raw.bucket

      # One prefix per Kafka-fed domain. The handler reads these rather than
      # hard-coding paths so the raw layout stays a Terraform concern.
      RAW_PREFIX_ADSB     = local.raw_prefixes.adsb
      RAW_PREFIX_OPENSKY  = local.raw_prefixes.opensky
      RAW_PREFIX_AIRLABS  = local.raw_prefixes.airlabs
      RAW_PREFIX_METAR    = local.raw_prefixes.metar
      RAW_PREFIX_EUROSTAT = local.raw_prefixes.eurostat

      # Upstream (managed-stack) lake bucket the export reads from.
      SOURCE_BUCKET = local.source_bucket

      LOG_LEVEL = var.lambda_log_level
    }
  }

  # Create the log group first so Lambda never creates an unmanaged, never-expire
  # group of its own (which would race Terraform on the next apply).
  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda,
  ]

  tags = {
    Name  = local.lambda_name
    Stage = "1-export-raw"
  }
}

# ── Scheduled + manual invocation ────────────────────────────────────────────

resource "aws_scheduler_schedule" "bronze_export" {
  count = var.lambda_schedule_enabled ? 1 : 0

  name        = "${local.name_prefix}-bronze-export"
  description = "Invoke the Bronze export Lambda on a ${var.lambda_schedule_expression} schedule"
  group_name  = "default"

  schedule_expression          = var.lambda_schedule_expression
  schedule_expression_timezone = "UTC"

  # Fire as close to the tick as possible. Switching to FLEXIBLE_WINDOW would
  # trade punctuality for a lower chance of a throttle storm, which this job
  # does not need.
  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.bronze_export.arn
    role_arn = aws_iam_role.scheduler.arn
  }

  # aws_scheduler_schedule has no `tags` argument in the 5.x provider; the
  # schedule is still tagged by name/prefix convention.
}

# Resource-based half of the "who may invoke this function" question: the
# schedule above invokes it through its execution role, and this pins the
# permission to that one schedule ARN.
resource "aws_lambda_permission" "allow_scheduler" {
  count = var.lambda_schedule_enabled ? 1 : 0

  statement_id  = "AllowEventBridgeScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bronze_export.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.bronze_export[0].arn
}
