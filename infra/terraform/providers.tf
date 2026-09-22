provider "aws" {
  region = var.aws_region

  # Every taggable resource inherits these. Resource-level `tags` merge over the
  # top, so an individual resource can still override Environment in a staging
  # workspace.
  default_tags {
    tags = local.tags
  }
}
