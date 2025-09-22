# Account/region lookups used for globally-unique names and ARNs.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  name_prefix = var.name_prefix
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.name

  # var.tags is the operator's escape hatch; the three managed tags are not
  # overridable so cost allocation reports stay consistent.
  tags = merge(
    var.tags,
    {
      Project     = "eu-air-traffic"
      Environment = var.environment
      ManagedBy   = "terraform"
    },
  )

  # Standard AZs only (skip opt-in/local zones so subnet math is predictable).
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  nat_gateway_count = var.single_nat_gateway ? 1 : var.az_count

  # S3 bucket names are global. Namespacing with the account id lets multiple
  # accounts run this module without a collision; an explicit name still wins.
  lake_bucket_name = var.lake_bucket_name != "" ? var.lake_bucket_name : "${local.name_prefix}-lake-${local.account_id}"
  web_bucket_name  = var.web_bucket_name != "" ? var.web_bucket_name : "${local.name_prefix}-web-${local.account_id}"

  # Images default to the ECR repos this module creates, tagged with image_tag.
  # Set collector_image/lake_image to pin a digest or a mirror.
  collector_image = var.collector_image != "" ? var.collector_image : "${aws_ecr_repository.collector.repository_url}:${var.image_tag}"
  lake_image      = var.lake_image != "" ? var.lake_image : "${aws_ecr_repository.lake.repository_url}:${var.image_tag}"

  # MSK SASL/SCRAM over TLS is the client endpoint. kafka-python-ng speaks
  # SASL_SSL + SCRAM-SHA-512 natively, so the app needs no signer plugin.
  msk_bootstrap_brokers = aws_msk_cluster.main.bootstrap_brokers_sasl_scram
  msk_client_port       = 9096

  # Kafka replication factors must not exceed the broker count or MSK rejects
  # the configuration. Three brokers is the production default; a 1-broker
  # sandbox degrades to a valid single-replica config instead of failing.
  msk_replication_factor  = min(var.msk_broker_count, 3)
  msk_min_insync_replicas = local.msk_replication_factor > 1 ? 2 : 1

  # Networking the lake step function and the collector both use.
  private_subnet_ids  = aws_subnet.private[*].id
  database_subnet_ids = aws_subnet.database[*].id

  # Dashboard-facing live API URL. If the operator has not set an explicit URL
  # we derive one from the ALB, using HTTPS only when a certificate exists.
  live_api_public_url = var.live_api_public_url != "" ? var.live_api_public_url : (
    var.acm_certificate_arn != "" ? "https://${aws_lb.main.dns_name}" : "http://${aws_lb.main.dns_name}"
  )

  # GitHub Actions OIDC provider: reuse an existing one or the one we create.
  github_oidc_provider_arn = var.create_github_oidc_provider ? one(aws_iam_openid_connect_provider.github[*].arn) : var.github_oidc_provider_arn
}
