# ─────────────────────────────────────────────────────────────────────────────
# Terraform and provider pins for the serverless analytics stack.
#
# This is a *separate* root module from ../../terraform (the managed stack:
# VPC/MSK/ECS/Aurora/Redshift/CloudFront). It deliberately shares no state and
# creates no networking: it is the cheap S3 + Glue + Athena path from the
# architecture diagram, and can be deployed alongside the managed stack or on
# its own.
# ─────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }

    # Zips ../../aws/lambda/bronze_export/ into the Lambda deployment package.
    # The Lambda source lives in the application repo, not in this module; the
    # archive provider reads it at plan/apply time so the function always ships
    # the code that is checked in next to it.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Remote state is intentionally not configured: the module runs out of the box
  # with local state. For anything shared, copy backend.tf.example from the
  # managed stack and fill it in (Terraform 1.10 supports native S3 locking with
  # `use_lockfile`, so no DynamoDB table is required).
}
