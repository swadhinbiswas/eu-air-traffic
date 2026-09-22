# ─────────────────────────────────────────────────────────────────────────────
# CloudFront in front of the dashboard S3 bucket.
#
# The bucket stays private. CloudFront reaches it through an Origin Access
# Control (OAC) using SigV4, and the bucket policy only allows the
# cloudfront.amazonaws.com service principal from this specific distribution.
# No public bucket policy, no S3 website endpoint, no legacy Origin Access
# Identity.
#
# SPA routing: S3 + OAC answers 403 (not 404) for a missing key, so both codes
# are mapped to index.html with a 200 for the React router to take over. The
# build writes long-lived immutable Cache-Control on hashed assets and no-cache
# on index.html; the managed cache policy honours those headers.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${local.name_prefix}-web-oac"
  description                       = "Origin Access Control for the dashboard S3 origin"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  comment             = "EU Air Traffic dashboard"

  aliases     = var.cloudfront_aliases
  price_class = var.cloudfront_price_class

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3-web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress               = true
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Default certificate until a custom domain is supplied. The ACM certificate
  # for CloudFront must be in us-east-1, regardless of where the bucket lives.
  viewer_certificate {
    acm_certificate_arn            = var.cloudfront_acm_certificate_arn != "" ? var.cloudfront_acm_certificate_arn : null
    cloudfront_default_certificate = var.cloudfront_acm_certificate_arn == "" ? true : false
    ssl_support_method             = var.cloudfront_acm_certificate_arn != "" ? "sni-only" : null
    minimum_protocol_version       = var.cloudfront_acm_certificate_arn != "" ? "TLSv1.2_2021" : null
  }

  tags = {
    Name = "${local.name_prefix}-web"
  }
}

data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid    = "AllowCloudFrontOAC"
    effect = "Allow"

    actions = ["s3:GetObject"]

    resources = ["${aws_s3_bucket.web.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json

  # The public-access block must exist first; block_public_policy rejects a
  # policy operation issued before it is in place.
  depends_on = [aws_s3_bucket_public_access_block.web]
}
