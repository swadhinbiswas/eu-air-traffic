# ─────────────────────────────────────────────────────────────────────────────
# Step Functions: the lake cycle.
#
# One ECS task runs scripts/run_lake.sh end to end; Step Functions owns retries
# and the failure path to SNS. See docs/aws-deployment.md §7.1.
#
# Single writer. DuckDB and Iceberg both assume one writer per table, and the
# repo's own concurrency guard (`cancel-in-progress: false`) exists for the same
# reason. EventBridge Scheduler has no concurrency control and a Standard state
# machine has no max-concurrency setting, so the first state takes an atomic
# lock in DynamoDB with a conditional PutItem: a second execution that arrives
# while one is running gets ConditionalCheckFailedException and exits cleanly.
# The lock is released on both the success and failure paths.
#
# Caveat: if an execution is force-stopped externally, the release states never
# run and the lock lingers. Clear it with:
#   aws dynamodb delete-item --table-name <name>-lake-lock \
#     --key '{"lock_id":{"S":"lake"}}'
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "lake_lock" {
  name         = "${local.name_prefix}-lake-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "lock_id"

  attribute {
    name = "lock_id"
    type = "S"
  }

  tags = {
    Name    = "${local.name_prefix}-lake-lock"
    Purpose = "single-writer-lock"
  }
}

locals {
  lake_state_machine_definition = {
    Comment = "EU Air Traffic lake cycle: one ECS Fargate task, one writer at a time."
    StartAt = "AcquireLock"

    States = {
      AcquireLock = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:dynamodb:putItem"

        Parameters = {
          TableName           = aws_dynamodb_table.lake_lock.name
          Item                = { lock_id = { S = "lake" } }
          ConditionExpression = "attribute_not_exists(lock_id)"
        }

        ResultPath = null

        Catch = [
          {
            ErrorEquals = ["DynamoDB.ConditionalCheckFailedException"]
            Next        = "AlreadyRunning"
          },
        ]

        Next = "RunLakeCycle"
      }

      AlreadyRunning = {
        Type    = "Succeed"
        Comment = "A lake cycle is already running; this schedule tick is skipped."
      }

      RunLakeCycle = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"

        Parameters = {
          Cluster         = aws_ecs_cluster.main.arn
          TaskDefinition  = aws_ecs_task_definition.lake.arn
          LaunchType      = "FARGATE"
          PlatformVersion = "1.4.0"
          PropagateTags   = "TASK_DEFINITION"

          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = local.private_subnet_ids
              SecurityGroups = [aws_security_group.ecs.id]
              AssignPublicIp = "DISABLED"
            }
          }
        }

        Retry = [
          {
            ErrorEquals     = ["States.TaskFailed"]
            IntervalSeconds = 60
            MaxAttempts     = 2
            BackoffRate     = 2.0
          },
        ]

        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "AlertOnFailure"
          },
        ]

        Next = "ReleaseLock"
      }

      # Success path: release the lock, then succeed.
      ReleaseLock = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:dynamodb:deleteItem"

        Parameters = {
          TableName = aws_dynamodb_table.lake_lock.name
          Key       = { lock_id = { S = "lake" } }
        }

        ResultPath = null
        Next       = "Done"
      }

      AlertOnFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"

        Parameters = {
          TopicArn    = aws_sns_topic.alerts.arn
          Subject     = "EU Air Traffic lake cycle failed"
          "Message.$" = "States.Format('Lake cycle failed: {}', States.JsonToString($.error))"
        }

        Next = "ReleaseLockOnFailure"
      }

      # Failure path: release the lock before failing, or every later run is
      # blocked forever.
      ReleaseLockOnFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:dynamodb:deleteItem"

        Parameters = {
          TableName = aws_dynamodb_table.lake_lock.name
          Key       = { lock_id = { S = "lake" } }
        }

        ResultPath = null
        Next       = "Fail"
      }

      Fail = {
        Type  = "Fail"
        Error = "LakeCycleFailed"
        Cause = "The lake ECS task failed after retries; see the execution history and CloudWatch logs."
      }

      Done = {
        Type = "Succeed"
      }
    }
  }
}

resource "aws_sfn_state_machine" "lake" {
  name       = "${local.name_prefix}-lake"
  type       = "STANDARD"
  role_arn   = aws_iam_role.sfn.arn
  definition = jsonencode(local.lake_state_machine_definition)

  tags = {
    Name     = "${local.name_prefix}-lake"
    Workload = "lake"
  }
}
