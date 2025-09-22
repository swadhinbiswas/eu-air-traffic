# ─────────────────────────────────────────────────────────────────────────────
# IAM: task roles, execution role, orchestration roles, Glue role, and the
# GitHub Actions OIDC deploy role.
#
# Least privilege is the whole point here. The collector can read its own API
# keys and the Kafka SCRAM secret and write logs — nothing else. The lake task
# can read/write the lake bucket, read the storage/serving/Kafka secrets and
# write logs. Neither can read the other's secret.
# ─────────────────────────────────────────────────────────────────────────────

# ── Shared trust policy for ECS task roles ───────────────────────────────────

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    sid     = "EcsTasksAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ── ECS execution role ───────────────────────────────────────────────────────
# Used by the ECS agent, not the application: pull the image, fetch the secrets
# listed in the task definition, and ship logs to CloudWatch.

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name_prefix}-ecs-execution"
  description        = "ECS agent role: ECR pull, secrets injection, log shipping"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = {
    Name = "${local.name_prefix}-ecs-execution"
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role must be able to read every secret a task definition
# injects. Listing them explicitly (rather than `*`) keeps a compromised task
# from reading unrelated account secrets.
data "aws_iam_policy_document" "ecs_execution_secrets" {
  statement {
    sid    = "ReadInjectedSecrets"
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue",
    ]

    resources = [
      aws_secretsmanager_secret.collector.arn,
      aws_secretsmanager_secret.lake.arn,
      aws_secretsmanager_secret.msk_scram.arn,
      aws_secretsmanager_secret.aurora_reader.arn,
      aws_rds_cluster.aurora.master_user_secret[0].secret_arn,
    ]
  }

  statement {
    sid    = "DecryptManagedSecrets"
    effect = "Allow"

    actions = [
      "kms:Decrypt",
    ]

    # Secrets Manager uses the AWS-managed key by default; this only matters if
    # an operator switches the secrets to a customer-managed CMK.
    resources = ["arn:aws:kms:${var.aws_region}:${local.account_id}:key/*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name   = "${local.name_prefix}-ecs-execution-secrets"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution_secrets.json
}

# ── Collector task role ──────────────────────────────────────────────────────
# The collector process needs no AWS API access at runtime: its secrets are
# injected by the execution role. It can only read its own secret and Kafka's,
# and write its own logs.
#
# Note: this role currently exists to shut the door, not to open one. If a
# later collector reads the snapshot from S3 or DynamoDB, add that here and
# nothing else.

resource "aws_iam_role" "collector_task" {
  name               = "${local.name_prefix}-collector-task"
  description        = "Collector runtime role: own secret + Kafka credentials + logs"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = {
    Name     = "${local.name_prefix}-collector-task"
    Workload = "collector"
  }
}

data "aws_iam_policy_document" "collector_task" {
  statement {
    sid    = "ReadCollectorAndKafkaSecrets"
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue",
    ]

    resources = [
      aws_secretsmanager_secret.collector.arn,
      aws_secretsmanager_secret.msk_scram.arn,
    ]
  }

  statement {
    sid    = "WriteCollectorLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.collector.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "collector_task" {
  name   = "${local.name_prefix}-collector-task"
  role   = aws_iam_role.collector_task.id
  policy = data.aws_iam_policy_document.collector_task.json
}

# ── Lake task role ───────────────────────────────────────────────────────────
# The lake job reads/writes Bronze and Silver in S3, reads the storage and
# serving secrets plus the Kafka consumer credentials, and writes logs. It
# cannot read the upstream API keys.

resource "aws_iam_role" "lake_task" {
  name               = "${local.name_prefix}-lake-task"
  description        = "Lake job role: S3 lake, storage/serving/Kafka secrets, logs"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = {
    Name     = "${local.name_prefix}-lake-task"
    Workload = "lake"
  }
}

data "aws_iam_policy_document" "lake_task" {
  statement {
    sid    = "ListLakeBucket"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:ListBucketMultipartUploads",
    ]

    resources = [
      aws_s3_bucket.lake.arn,
    ]
  }

  statement {
    sid    = "ReadWriteLakeObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]

    resources = [
      "${aws_s3_bucket.lake.arn}/*",
    ]
  }

  statement {
    sid    = "ReadLakeAndKafkaSecrets"
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue",
    ]

    resources = [
      aws_secretsmanager_secret.lake.arn,
      aws_secretsmanager_secret.msk_scram.arn,
      aws_secretsmanager_secret.aurora_reader.arn,
      aws_rds_cluster.aurora.master_user_secret[0].secret_arn,
    ]
  }

  statement {
    sid    = "WriteLakeLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.lake.arn}:*",
    ]
  }

  # The freshness alarm watches a custom metric the lake task emits at the end of
  # every successful cycle (scripts/publish_metrics.py). PutMetricData has no
  # resource-level permissions, so the resource is "*" per AWS documentation.
  statement {
    sid    = "PublishFreshnessMetrics"
    effect = "Allow"

    actions = [
      "cloudwatch:PutMetricData",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lake_task" {
  name   = "${local.name_prefix}-lake-task"
  role   = aws_iam_role.lake_task.id
  policy = data.aws_iam_policy_document.lake_task.json
}

# ── Step Functions role ──────────────────────────────────────────────────────

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    sid     = "StatesAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.name_prefix}-lake-sfn"
  description        = "Runs the lake ECS task, publishes failures to SNS, holds the single-writer lock"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json

  tags = {
    Name = "${local.name_prefix}-lake-sfn"
  }
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid    = "RunLakeTask"
    effect = "Allow"

    actions = [
      "ecs:RunTask",
    ]

    resources = [
      aws_ecs_task_definition.lake.arn,
    ]
  }

  statement {
    sid    = "ObserveAndStopTasks"
    effect = "Allow"

    actions = [
      "ecs:DescribeTasks",
      "ecs:StopTask",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "PassTaskRoles"
    effect = "Allow"

    actions = [
      "iam:PassRole",
    ]

    resources = [
      aws_iam_role.ecs_execution.arn,
      aws_iam_role.lake_task.arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid    = "PublishFailureAlerts"
    effect = "Allow"

    actions = [
      "sns:Publish",
    ]

    resources = [
      aws_sns_topic.alerts.arn,
    ]
  }

  statement {
    sid    = "SingleWriterLock"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]

    resources = [
      aws_dynamodb_table.lake_lock.arn,
    ]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${local.name_prefix}-lake-sfn"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

# ── EventBridge Scheduler role ───────────────────────────────────────────────

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    sid     = "SchedulerAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name_prefix}-scheduler"
  description        = "Starts the lake state machine on the 15-minute schedule"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json

  tags = {
    Name = "${local.name_prefix}-scheduler"
  }
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid    = "StartLakeExecution"
    effect = "Allow"

    actions = [
      "states:StartExecution",
    ]

    resources = [
      aws_sfn_state_machine.lake.arn,
    ]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${local.name_prefix}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

# ── Glue crawler role ────────────────────────────────────────────────────────

data "aws_iam_policy_document" "glue_assume" {
  statement {
    sid     = "GlueAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue" {
  name               = "${local.name_prefix}-glue"
  description        = "Glue crawler role for the Silver and Gold prefixes"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json

  tags = {
    Name = "${local.name_prefix}-glue"
  }
}

resource "aws_iam_role_policy_attachment" "glue_managed" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "glue" {
  statement {
    sid    = "ReadLakeForCrawling"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.lake.arn,
      "${aws_s3_bucket.lake.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "glue" {
  name   = "${local.name_prefix}-glue"
  role   = aws_iam_role.glue.id
  policy = data.aws_iam_policy_document.glue.json
}

# ── GitHub Actions OIDC deploy role ──────────────────────────────────────────
# No long-lived AWS keys in CI. The trust policy only accepts the web-identity
# token for this repo's deploy branch, so a fork or a feature branch cannot
# assume the role. See docs/aws-deployment.md §11.

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [var.github_oidc_thumbprint]

  tags = {
    Name = "github-actions"
  }
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    sid     = "GitHubActionsWebIdentity"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/${var.github_deploy_branch}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${local.name_prefix}-github-deploy"
  description        = "GitHub Actions deploy role: push images, update the collector, trigger the lake"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json

  tags = {
    Name = "${local.name_prefix}-github-deploy"
  }
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid    = "EcrLogin"
    effect = "Allow"

    actions = [
      "ecr:GetAuthorizationToken",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "PushImages"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]

    resources = [
      aws_ecr_repository.collector.arn,
      aws_ecr_repository.lake.arn,
    ]
  }

  statement {
    sid    = "RegisterTaskDefinitions"
    effect = "Allow"

    actions = [
      "ecs:RegisterTaskDefinition",
      "ecs:DescribeTaskDefinition",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "DeployCollectorService"
    effect = "Allow"

    actions = [
      "ecs:UpdateService",
      "ecs:DescribeServices",
    ]

    resources = [
      "arn:aws:ecs:${var.aws_region}:${local.account_id}:service/${aws_ecs_cluster.main.name}/${aws_ecs_service.collector.name}",
    ]
  }

  statement {
    sid    = "PassTaskRolesWhenRegistering"
    effect = "Allow"

    actions = [
      "iam:PassRole",
    ]

    resources = [
      aws_iam_role.ecs_execution.arn,
      aws_iam_role.collector_task.arn,
      aws_iam_role.lake_task.arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid    = "TriggerAndInspectLake"
    effect = "Allow"

    actions = [
      "states:StartExecution",
      "states:DescribeExecution",
      "states:DescribeStateMachine",
    ]

    resources = [
      aws_sfn_state_machine.lake.arn,
      "arn:aws:states:${var.aws_region}:${local.account_id}:execution:${aws_sfn_state_machine.lake.name}:*",
    ]
  }

  # Publishing the dashboard additionally needs s3:PutObject on the web bucket
  # and cloudfront:CreateInvalidation. Those are omitted here: the CI deploy
  # workflow does not touch the dashboard today. Add a narrowly-scoped
  # statement for the web bucket ARN when it does.
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "${local.name_prefix}-github-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}
