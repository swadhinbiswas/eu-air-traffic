provider "aws" {
  region = var.aws_region

  # Every taggable resource inherits these, including the Stack tag that keeps
  # serverless-analytics resources distinguishable from the managed stack in
  # Cost Explorer. Resource-level `tags` merge over the top.
  default_tags {
    tags = local.tags
  }
}
