# ─────────────────────────────────────────────────────────────────────────────
# IAM: four least-privilege roles, one per actor.
#
#   <prefix>-lambda-bronze-export  read the upstream lake, write the raw zone
#   <prefix>-scheduler             invoke exactly the one export function
#   <prefix>-glue-crawler          read the raw zone, populate the raw catalog
#   <prefix>-glue-job              read raw + artifacts, write curated (+ catalog)
#
# Resources are named explicitly everywhere it is possible. The two deliberate
# wildcards are CloudWatch PutMetricData and the Glue catalog *actions* whose API
# does not support resource-level scoping; both are called out inline.
# ─────────────────────────────────────────────────────────────────────────────

# ── Assume-role policies ─────────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    sid     = "LambdaAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

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

# ── Stage 1: export Lambda ───────────────────────────────────────────────────

resource "aws_iam_role" "lambda" {
  name               = "${local.name_prefix}-lambda-bronze-export"
  description        = "Export job: read the upstream lake, write the raw S3 zone, emit logs"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = {
    Name     = "${local.name_prefix}-lambda-bronze-export"
    Workload = "bronze-export"
  }
}

data "aws_iam_policy_document" "lambda" {
  # Logs are written to the one group Terraform creates, never to `*`.
  statement {
    sid    = "WriteFunctionLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  # Bronze-layer export reads the managed stack's lake objects.
  statement {
    sid    = "ReadUpstreamLake"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]

    resources = [
      local.source_bucket_arn,
      "${local.source_bucket_arn}/*",
    ]
  }

  # ...and lands Parquet under the five domain prefixes of the raw bucket.
  statement {
    sid    = "WriteRawZone"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]

    resources = [
      aws_s3_bucket.raw.arn,
      "${aws_s3_bucket.raw.arn}/*",
    ]
  }

  # The freshness alarm reads this custom metric; publishing it needs `*`
  # because PutMetricData has no resource-level ARN.
  statement {
    sid    = "PublishFreshnessMetric"
    effect = "Allow"

    actions = [
      "cloudwatch:PutMetricData",
    ]

    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [var.stale_data_metric_namespace]
    }
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.name_prefix}-lambda-bronze-export"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

# ── EventBridge Scheduler execution role ─────────────────────────────────────

resource "aws_iam_role" "scheduler" {
  name               = "${local.name_prefix}-scheduler"
  description        = "EventBridge Scheduler role: invoke the Bronze export function"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json

  tags = {
    Name     = "${local.name_prefix}-scheduler"
    Workload = "scheduler"
  }
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "InvokeExportFunction"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.bronze_export.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${local.name_prefix}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

# ── Stage 3: raw-zone Glue crawler ───────────────────────────────────────────

resource "aws_iam_role" "glue_crawler" {
  name               = "${local.name_prefix}-glue-crawler"
  description        = "Glue crawler: read the raw S3 zone, populate the raw catalog database"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json

  tags = {
    Name     = "${local.name_prefix}-glue-crawler"
    Workload = "crawler"
  }
}

data "aws_iam_policy_document" "glue_crawler" {
  statement {
    sid    = "ReadRawZone"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetBucketLocation",
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.raw.arn,
      "${aws_s3_bucket.raw.arn}/*",
    ]
  }

  # Catalog writes are confined to the raw database. The resource list carries
  # catalog / database / table ARNs because Glue splits actions across all three
  # resource types and no single ARN satisfies them all.
  statement {
    sid    = "PopulateRawCatalog"
    effect = "Allow"

    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:CreateDatabase",
      "glue:UpdateDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
      "glue:CreatePartition",
      "glue:BatchCreatePartition",
      "glue:UpdatePartition",
      "glue:BatchUpdatePartition",
      "glue:GetCatalogImportStatus",
    ]

    resources = [
      local.glue_catalog_arn,
      local.glue_raw_database_arn,
      local.glue_raw_table_arn,
    ]
  }

  statement {
    sid    = "WriteCrawlerLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws-glue/crawlers*:*"]
  }
}

resource "aws_iam_role_policy" "glue_crawler" {
  name   = "${local.name_prefix}-glue-crawler"
  role   = aws_iam_role.glue_crawler.id
  policy = data.aws_iam_policy_document.glue_crawler.json
}

# ── Stage 4: Glue ETL job ────────────────────────────────────────────────────

resource "aws_iam_role" "glue_job" {
  name               = "${local.name_prefix}-glue-job"
  description        = "Glue ETL: read raw + script, write curated Parquet and the curated catalog"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json

  tags = {
    Name     = "${local.name_prefix}-glue-job"
    Workload = "transform"
  }
}

data "aws_iam_policy_document" "glue_job" {
  statement {
    sid    = "ReadRawZone"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetBucketLocation",
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.raw.arn,
      "${aws_s3_bucket.raw.arn}/*",
    ]
  }

  statement {
    sid    = "WriteCuratedZone"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:GetBucketLocation",
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.curated.arn,
      "${aws_s3_bucket.curated.arn}/*",
    ]
  }

  # The job reads its own PySpark script from the artifacts bucket and uses a
  # GLUE TempDir to spill shuffle data there. DeleteObject is for that spill.
  statement {
    sid    = "ReadScriptAndUseTempDir"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]

    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
    ]
  }

  # Read the raw catalog, write the curated catalog. Raw writes are deliberately
  # omitted: the transform job has no business mutating Bronze metadata.
  statement {
    sid    = "ReadRawCatalog"
    effect = "Allow"

    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
    ]

    resources = [
      local.glue_catalog_arn,
      local.glue_raw_database_arn,
      local.glue_raw_table_arn,
    ]
  }

  statement {
    sid    = "WriteCuratedCatalog"
    effect = "Allow"

    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
      "glue:CreatePartition",
      "glue:BatchCreatePartition",
      "glue:UpdatePartition",
      "glue:BatchUpdatePartition",
    ]

    resources = [
      local.glue_catalog_arn,
      local.glue_curated_database_arn,
      local.glue_curated_table_arn,
    ]
  }

  statement {
    sid    = "WriteJobLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.glue_job.arn}:*",
      "${aws_cloudwatch_log_group.glue_job_error.arn}:*",
    ]
  }

  # Glue job metrics (the failure alarm below reads them). PutMetricData has no
  # resource-level ARN, so `*` is required by the API.
  statement {
    sid    = "PublishJobMetrics"
    effect = "Allow"

    actions = [
      "cloudwatch:PutMetricData",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "glue_job" {
  name   = "${local.name_prefix}-glue-job"
  role   = aws_iam_role.glue_job.id
  policy = data.aws_iam_policy_document.glue_job.json
}
