# ─────────────────────────────────────────────────────────────────────────────
# Observability: log groups, the alert topic, and three alarms.
#
# The failure signals that actually matter here are:
#   1. the export Lambda erroring (stage 1 is the only moving part that can fail
#      transiently against an upstream API),
#   2. the Glue transform failing (stage 4 writes the curated zone), and
#   3. raw data going stale — the silent failure this pipeline will actually hit.
#
# (1) and (2) are wired to an EventBridge rule as well as a metric alarm: Glue's
# job metrics are useful but not a guaranteed per-run failure signal, whereas the
# `Glue Job State Change` event always fires. The event rule is the real alarm;
# the metric alarm is a second, cheaper-to-read signal. (3) is a skeleton until
# the export job starts publishing the custom metric — see the comment below.
# ─────────────────────────────────────────────────────────────────────────────

# ── Alert topic ──────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-serverless-alerts"

  tags = {
    Name = "${local.name_prefix}-serverless-alerts"
  }
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count = var.alarm_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# CloudWatch and EventBridge publish through service principals, which the
# default topic policy does not allow. The SourceAccount condition keeps any
# other account's EventBridge from using this topic.
data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AllowCloudWatchAndEventBridgePublish"
    effect    = "Allow"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  # The account owner keeps full control so Terraform can still manage and
  # destroy the topic after the service statement is in place.
  statement {
    sid       = "AllowAccountOwner"
    effect    = "Allow"
    actions   = ["SNS:*"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

# ── Log groups ───────────────────────────────────────────────────────────────
# Created here (not left to the services) so retention is managed and the groups
# are tagged with the rest of the stack.

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = var.log_retention_days

  tags = {
    Name     = local.lambda_name
    Workload = "bronze-export"
  }
}

resource "aws_cloudwatch_log_group" "glue_job" {
  name              = "/aws-glue/jobs/${local.glue_job_name}"
  retention_in_days = var.log_retention_days

  tags = {
    Name     = local.glue_job_name
    Workload = "transform"
  }
}

# Glue writes job errors here independently of the continuous-log group above.
resource "aws_cloudwatch_log_group" "glue_job_error" {
  name              = "/aws-glue/jobs/error"
  retention_in_days = var.log_retention_days

  tags = {
    Name     = "${local.glue_job_name}-error"
    Workload = "transform"
  }
}

# ── Alarm: export Lambda errors ──────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${local.name_prefix}-bronze-export-errors"
  alarm_description   = "Bronze export Lambda returned one or more errors in 5 minutes"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.lambda_error_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.bronze_export.function_name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name     = "${local.name_prefix}-bronze-export-errors"
    Workload = "bronze-export"
  }
}

# ── Alarm: Glue transform failed tasks (metric) ──────────────────────────────
# Namespace "Glue", not "AWS/Glue". `numFailedTasks` is a task-level counter and
# can be zero even on a script-level failure, which is exactly why the
# EventBridge rule below exists as the primary signal.

resource "aws_cloudwatch_metric_alarm" "glue_failed_tasks" {
  alarm_name          = "${local.name_prefix}-transform-failed-tasks"
  alarm_description   = "Glue transform reported one or more failed tasks in 5 minutes"
  namespace           = "Glue"
  metric_name         = "glue.driver.aggregate.numFailedTasks"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.glue_failed_tasks_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  # Glue publishes driver aggregate metrics with the dimension set
  # {JobName, JobRunId, Type}; JobRunId = "ALL" is the per-job aggregation.
  # Confirm the exact dimensions for your Glue version in the CloudWatch console
  # if this alarm sits at INSUFFICIENT_DATA.
  dimensions = {
    JobName  = aws_glue_job.transform.name
    JobRunId = "ALL"
    Type     = "count"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name     = "${local.name_prefix}-transform-failed-tasks"
    Workload = "transform"
  }
}

# ── Alarm: Glue transform terminal failure (event) ───────────────────────────
# Every Glue job emits a `Glue Job State Change` event on the default event bus.
# This captures FAILED/TIMEOUT/STOPPED for this one job and forwards the raw
# event to the alert topic, so the notification carries the error message.

resource "aws_cloudwatch_event_rule" "glue_job_state" {
  count = var.enable_glue_event_alarm ? 1 : 0

  name        = "${local.name_prefix}-glue-job-state"
  description = "Glue transform job reached a terminal failure state"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [aws_glue_job.transform.name]
      state   = ["FAILED", "TIMEOUT", "STOPPED"]
    }
  })

  tags = {
    Name     = "${local.name_prefix}-glue-job-state"
    Workload = "transform"
  }
}

resource "aws_cloudwatch_event_target" "glue_job_state" {
  count = var.enable_glue_event_alarm ? 1 : 0

  rule      = aws_cloudwatch_event_rule.glue_job_state[0].name
  target_id = "notify-alerts-topic"
  arn       = aws_sns_topic.alerts.arn
}

# ── Alarm skeleton: raw data freshness ───────────────────────────────────────
# Raw freshness is the failure this pipeline is most likely to hit and least
# likely to notice: the Lambda can succeed while exporting nothing because an
# upstream API key expired. Nothing publishes this metric yet, so the alarm stays
# INSUFFICIENT_DATA (and, with treat_missing_data = notBreaching, stays quiet).
#
# To bring it to life, have aws/lambda/bronze_export/handler.py write the age in
# seconds of the newest raw object at the end of a successful run:
#
#   cloudwatch.put_metric_data(
#       Namespace = stale_data_metric_namespace,          # default EUAirTraffic/Serverless
#       MetricData = [{"MetricName": stale_data_metric_name,   # default RawDataAgeSeconds
#                      "Value": age_seconds, "Unit": "Seconds"}])
#
# The threshold defaults to twice the hourly export cadence.

resource "aws_cloudwatch_metric_alarm" "raw_data_stale" {
  count = var.enable_stale_data_alarm ? 1 : 0

  alarm_name          = "${local.name_prefix}-raw-data-stale"
  alarm_description   = "Newest raw object is older than ${var.stale_data_threshold_seconds}s — the export job may be silently exporting nothing"
  namespace           = var.stale_data_metric_namespace
  metric_name         = var.stale_data_metric_name
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.stale_data_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name     = "${local.name_prefix}-raw-data-stale"
    Workload = "bronze-export"
  }
}
