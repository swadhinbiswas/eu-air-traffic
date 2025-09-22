# ─────────────────────────────────────────────────────────────────────────────
# S3: the data lake and the dashboard origin.
#
#   <name_prefix>-lake-<account>   Bronze/Silver/Gold Parquet + checkpoints
#   <name_prefix>-web-<account>    the React build, served only through CloudFront
# ─────────────────────────────────────────────────────────────────────────────

# ── Lake bucket ──────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "lake" {
  bucket        = local.lake_bucket_name
  force_destroy = var.lake_bucket_force_destroy

  tags = {
    Name    = local.lake_bucket_name
    Purpose = "data-lake"
  }
}

# BucketOwnerEnforced disables ACLs entirely and makes the bucket owner the
# owner of every object, which is what both OAC and Lake Formation expect.
resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# The lake must never be public. CloudFront reaches the web bucket via OAC, and
# the lake is only ever reached by IAM-authenticated tasks.
resource "aws_s3_bucket_public_access_block" "lake" {
  bucket = aws_s3_bucket.lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  # Versioning must exist before a lifecycle rule can act on noncurrent versions.
  depends_on = [aws_s3_bucket_versioning.lake]

  # Bronze is append-only and read rarely once Silver is built: tier it down.
  rule {
    id     = "bronze-tiering"
    status = "Enabled"

    filter {
      prefix = "bronze/"
    }

    transition {
      days          = var.bronze_ia_transition_days
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = var.bronze_glacier_transition_days
      storage_class = "GLACIER_IR"
    }

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER_IR"
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }
  }

  # Failed multipart uploads from an interrupted lake task would otherwise sit
  # invisible and bill forever.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ── Dashboard bucket ─────────────────────────────────────────────────────────
# Private bucket. The only reader is the CloudFront distribution, which uses an
# Origin Access Control and SigV4 — no public bucket policy, no website endpoint.

resource "aws_s3_bucket" "web" {
  bucket        = local.web_bucket_name
  force_destroy = var.web_bucket_force_destroy

  tags = {
    Name    = local.web_bucket_name
    Purpose = "dashboard"
  }
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "web" {
  bucket = aws_s3_bucket.web.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket = aws_s3_bucket.web.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
