# ─────────────────────────────────────────────────────────────────────────────
# S3: the three buckets this stack owns.
#
#   <prefix>-raw-<account>        stages 2  landing zone (adsb/, opensky/, ...)
#   <prefix>-curated-<account>    stage 5  curated zone (fact/, dim/, ...)
#   <prefix>-artifacts-<account>  Glue ETL script + Athena query results
#
# All three are private, encrypted at rest, versioned, and enforced
# BucketOwnerEnforced (ACLs disabled — the modern default, and what Athena and
# Glue expect). The upstream lake bucket the export job reads is owned by the
# managed stack and is not managed here.
# ─────────────────────────────────────────────────────────────────────────────

# ── Raw / landing zone ───────────────────────────────────────────────────────

resource "aws_s3_bucket" "raw" {
  bucket        = local.raw_bucket_name
  force_destroy = var.raw_bucket_force_destroy

  tags = {
    Name = local.raw_bucket_name
    Zone = "raw"
  }
}

resource "aws_s3_bucket_ownership_controls" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket = aws_s3_bucket.raw.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# One tiering rule per domain, so a change to one feed's retention can be made
# without touching the others. Raw data is append-only and only re-read when a
# transform is backfilled, so it cools quickly.
resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  # Versioning must exist before the noncurrent-version rules below can act.
  depends_on = [aws_s3_bucket_versioning.raw]

  dynamic "rule" {
    for_each = local.raw_prefixes

    content {
      id     = "tier-${rule.key}"
      status = "Enabled"

      filter {
        prefix = rule.value
      }

      transition {
        days          = var.raw_ia_transition_days
        storage_class = "STANDARD_IA"
      }

      transition {
        days          = var.raw_glacier_transition_days
        storage_class = "GLACIER_IR"
      }
    }
  }

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER_IR"
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }
  }

  # Failed multipart uploads from an interrupted export would otherwise sit
  # invisible and bill forever.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_incomplete_multipart_upload_days
    }
  }
}

resource "aws_s3_bucket_policy" "raw" {
  bucket = aws_s3_bucket.raw.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}

# ── Curated zone ─────────────────────────────────────────────────────────────
# Curated data is what Athena queries, so it is deliberately *not* tiered to
# Glacier: a query against an archived object fails. Only noncurrent versions
# and abandoned multipart uploads are cleaned up.

resource "aws_s3_bucket" "curated" {
  bucket        = local.curated_bucket_name
  force_destroy = var.curated_bucket_force_destroy

  tags = {
    Name = local.curated_bucket_name
    Zone = "curated"
  }
}

resource "aws_s3_bucket_ownership_controls" "curated" {
  bucket = aws_s3_bucket.curated.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "curated" {
  bucket = aws_s3_bucket.curated.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "curated" {
  bucket = aws_s3_bucket.curated.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "curated" {
  bucket = aws_s3_bucket.curated.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "curated" {
  bucket = aws_s3_bucket.curated.id

  depends_on = [aws_s3_bucket_versioning.curated]

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER_IR"
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_incomplete_multipart_upload_days
    }
  }
}

resource "aws_s3_bucket_policy" "curated" {
  bucket = aws_s3_bucket.curated.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.curated.arn, "${aws_s3_bucket.curated.arn}/*"]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}

# ── Artifacts ────────────────────────────────────────────────────────────────
# Two prefixes, one purpose: hold the PySpark script Glue runs and the query
# results Athena writes. Results are regenerable, so they expire on a timer.

resource "aws_s3_bucket" "artifacts" {
  bucket        = local.artifacts_bucket_name
  force_destroy = var.artifacts_bucket_force_destroy

  tags = {
    Name = local.artifacts_bucket_name
    Zone = "artifacts"
  }
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  depends_on = [aws_s3_bucket_versioning.artifacts]

  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = var.athena_results_prefix
    }

    expiration {
      days = var.artifacts_expiration_days
    }
  }

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_incomplete_multipart_upload_days
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}
