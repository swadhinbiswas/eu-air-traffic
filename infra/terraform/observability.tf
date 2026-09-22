# ─────────────────────────────────────────────────────────────────────────────
# Observability: log groups, the alert topic, and the alarms that matter.
#
# The guiding rule from the docs is to alarm on *freshness*, not process
# liveness — a healthy collector with an expired upstream key is the failure
# this platform actually hits (docs/aws-deployment.md §12). Liveness alarms are
# still here as a fast signal, but the freshness alarm is the important one.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"

  tags = {
    Name = "${local.name_prefix}-alerts"
  }
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count = var.alarm_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ── Log groups ───────────────────────────────────────────────────────────────
# One per ECS workload plus the MSK broker logs. Created here so the task
# definitions never fight CloudWatch over an implicit group, and so retention
# is managed instead of "never expire".

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/ecs/${local.name_prefix}-collector"
  retention_in_days = var.log_retention_days

  tags = {
    Name     = "${local.name_prefix}-collector"
    Workload = "collector"
  }
}

resource "aws_cloudwatch_log_group" "lake" {
  name              = "/ecs/${local.name_prefix}-lake"
  retention_in_days = var.log_retention_days

  tags = {
    Name     = "${local.name_prefix}-lake"
    Workload = "lake"
  }
}

resource "aws_cloudwatch_log_group" "msk" {
  name              = "/${local.name_prefix}/msk/broker"
  retention_in_days = var.log_retention_days

  tags = {
    Name     = "${local.name_prefix}-msk-broker"
    Workload = "kafka"
  }
}

# ── Collector: unhealthy targets behind the ALB ──────────────────────────────
# Fire when any target is unhealthy for two consecutive minutes. This catches a
# wedged live API before the deployment circuit breaker notices.

resource "aws_cloudwatch_metric_alarm" "collector_unhealthy_hosts" {
  alarm_name          = "${local.name_prefix}-collector-unhealthy-hosts"
  alarm_description   = "Collector /health failing: one or more ALB targets unhealthy for 2 minutes"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnhealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.collector.arn_suffix
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name     = "${local.name_prefix}-collector-unhealthy-hosts"
    Workload = "collector"
  }
}

# ── Collector: CPU saturation ────────────────────────────────────────────────
# The collector loops are I/O-bound, so sustained high CPU means the task is
# under-sized or stuck, either of which is worth knowing about.

resource "aws_cloudwatch_metric_alarm" "collector_cpu" {
  alarm_name          = "${local.name_prefix}-collector-cpu"
  alarm_description   = "Collector CPU above ${var.collector_cpu_alarm_threshold}% for 15 minutes"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.collector_cpu_alarm_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.collector.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name     = "${local.name_prefix}-collector-cpu"
    Workload = "collector"
  }
}

# ── Lake: failed state machine executions ────────────────────────────────────
# The state machine already publishes to SNS on its own failure path; this is
# the independent backstop for anything that fails before reaching that state
# (for example, a role that cannot be assumed).

resource "aws_cloudwatch_metric_alarm" "sfn_failed" {
  alarm_name          = "${local.name_prefix}-lake-executions-failed"
  alarm_description   = "One or more lake state machine executions failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.lake.arn
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name     = "${local.name_prefix}-lake-executions-failed"
    Workload = "lake"
  }
}

# ── MSK: consumer lag on the sink group ──────────────────────────────────────
# Provisioned MSK publishes OffsetLag per consumer group and topic. The sink's
# committed offset is the source of truth for "how far behind the lake is".
# If your account surfaces this metric under different dimensions, check the
# CloudWatch console for the AWS/Kafka namespace and adjust the dimensions map.

resource "aws_cloudwatch_metric_alarm" "msk_consumer_lag" {
  alarm_name          = "${local.name_prefix}-msk-consumer-lag"
  alarm_description   = "Sink group offset lag on ${var.kafka_topic_positions} above ${var.msk_consumer_lag_threshold}"
  namespace           = "AWS/Kafka"
  metric_name         = "OffsetLag"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.msk_consumer_lag_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    "Cluster Name"   = aws_msk_cluster.main.cluster_name
    "Consumer Group" = var.sink_consumer_group
    "Topic"          = var.kafka_topic_positions
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name     = "${local.name_prefix}-msk-consumer-lag"
    Workload = "kafka"
  }
}

# ── Lake: data freshness ─────────────────────────────────────────────────────
# The alarm that actually catches silent staleness: the pipeline report emits
# the age of the last successful cycle into a custom metric, and this fires when
# it exceeds twice the scheduler cadence. No dimensions: any writer emitting the
# metric in this namespace trips it.
#
# The metric is produced by scripts/publish_metrics.py, which runs at the end of
# scripts/run_lake.sh when LAKE_METRICS_ENABLED=1 (set in the lake task).

resource "aws_cloudwatch_metric_alarm" "lake_freshness" {
  alarm_name          = "${local.name_prefix}-lake-freshness"
  alarm_description   = "Last successful lake cycle older than ${var.lake_freshness_threshold_seconds}s"
  namespace           = var.lake_metrics_namespace
  metric_name         = var.lake_freshness_metric_name
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.lake_freshness_threshold_seconds
  comparison_operator = "GreaterThanThreshold"

  # Missing data is *not* treated as breaching here on purpose: a quiet first
  # deployment should not page anyone before the metric has ever been written.
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name     = "${local.name_prefix}-lake-freshness"
    Workload = "lake"
  }
}
