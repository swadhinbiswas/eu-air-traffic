# ─────────────────────────────────────────────────────────────────────────────
# EventBridge Scheduler: the 15-minute lake cadence.
#
# This replaces both the GitHub Actions `schedule` and the VPS dispatcher
# workaround for GitHub's best-effort scheduler. EventBridge is reliable, and it
# starts the state machine rather than a shell script, so retries and failure
# alerts are real. See docs/aws-deployment.md §7.1.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_scheduler_schedule" "lake" {
  count = var.enable_lake_schedule ? 1 : 0

  name       = "${local.name_prefix}-lake"
  state      = "ENABLED"
  group_name = "default"

  schedule_expression = "rate(15 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.lake.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ source = "eventbridge-scheduler" })

    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 2
    }
  }
}
