# Pin the Terraform core and the providers this root module uses.
#
# We deliberately pin the AWS provider to the 5.x line: the 6.x line renames a
# few arguments used here (notably the S3 lifecycle `filter` and several
# CloudFront fields) and this module is meant to be boring, not bleeding edge.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }

    # Used to generate the MSK SCRAM password and the Redshift admin password.
    # Everything the application itself consumes lives in Secrets Manager; the
    # random provider only exists so no human has to invent a 32-char secret.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }

    # Used by the opt-in Aurora read-only-role provisioner.
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }

  # Remote state is not configured here so the module works out of the box with
  # local state. For anything shared, copy `backend.tf.example` to `backend.tf`
  # and fill it in, or let the deploy workflow generate it from TF_STATE_BUCKET.
  #
  # required_version is 1.10 because the S3 backend example uses `use_lockfile`
  # (native S3 locking); older Terraform needs a DynamoDB lock table instead.
}
