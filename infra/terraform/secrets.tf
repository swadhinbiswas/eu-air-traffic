# ─────────────────────────────────────────────────────────────────────────────
# Secrets and non-secret configuration.
#
# One secret per workload, per docs/aws-deployment.md §2.1. The collector secret
# holds upstream API keys; the lake secret holds the storage/warehouse/serving
# credentials. Kafka credentials live in the MSK SCRAM secret created in msk.tf
# so both workloads read them from the single place MSK itself references.
#
# Terraform creates the secret *container* and an initial, empty version. It
# then ignores future changes so a human can populate the real values in the
# console (or with `aws secretsmanager put-secret-value`) without `apply`
# reverting them. Those values are the only manual step after the first apply.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_secretsmanager_secret" "collector" {
  name                    = "${local.name_prefix}/collector"
  description             = "Upstream API credentials for the collector task (OpenSky, AirLabs)"
  recovery_window_in_days = var.secrets_recovery_window_days

  tags = {
    Workload = "collector"
  }
}

resource "aws_secretsmanager_secret_version" "collector" {
  secret_id = aws_secretsmanager_secret.collector.id

  secret_string = jsonencode({
    OPENSKY_CLIENT_ID     = ""
    OPENSKY_CLIENT_SECRET = ""
    AIRLABS_API_KEY       = ""
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "lake" {
  name                    = "${local.name_prefix}/lake"
  description             = "Storage, warehouse and serving credentials for the lake task (Hugging Face, MotherDuck, Turso)"
  recovery_window_in_days = var.secrets_recovery_window_days

  tags = {
    Workload = "lake"
  }
}

resource "aws_secretsmanager_secret_version" "lake" {
  secret_id = aws_secretsmanager_secret.lake.id

  secret_string = jsonencode({
    HF_TOKEN         = ""
    MOTHERDUCK_TOKEN = ""
    TURSO_TARGETS    = "[]"
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Non-secret configuration in one Parameter Store value. The task definitions
# inject these directly as environment variables (Terraform renders them), but
# having the canonical copy in SSM means an operator can read exactly what the
# running deployment is configured for without opening the Terraform state.
resource "aws_ssm_parameter" "app_config" {
  name        = "/${local.name_prefix}/config"
  description = "Non-secret runtime configuration for the EU Air Traffic platform"
  type        = "String"

  value = jsonencode({
    ENVIRONMENT             = var.environment
    LOG_LEVEL               = var.log_level
    KAFKA_SECURITY_PROTOCOL = "SASL_SSL"
    KAFKA_SASL_MECHANISM    = "SCRAM-SHA-512"
    KAFKA_TOPIC_POSITIONS   = var.kafka_topic_positions
    KAFKA_TOPIC_FLIGHTS     = var.kafka_topic_flights
    KAFKA_TOPIC_WEATHER     = var.kafka_topic_weather
    KAFKA_TOPIC_FUEL        = var.kafka_topic_fuel
    KAFKA_TOPIC_REFERENCE   = var.kafka_topic_reference
    SINK_CONSUMER_GROUP     = var.sink_consumer_group
    LAKE_WINDOW_SECONDS     = var.lake_window_seconds
    KAFKA_RETENTION_HOURS   = var.kafka_retention_hours
    LAKE_BACKEND            = "s3"
    S3_BUCKET               = aws_s3_bucket.lake.bucket
    S3_PREFIX               = var.s3_prefix
    LIVE_API_HOST           = "0.0.0.0"
    LIVE_API_PORT           = var.collector_container_port
    LIVE_API_PUBLIC_URL     = local.live_api_public_url
    AWS_REGION              = var.aws_region
    MSK_BOOTSTRAP_BROKERS   = local.msk_bootstrap_brokers
    MSK_CLIENT_PORT         = local.msk_client_port
    AURORA_WRITER_ENDPOINT  = aws_rds_cluster.aurora.endpoint
    AURORA_READER_ENDPOINT  = aws_rds_cluster.aurora.reader_endpoint
    AURORA_DATABASE         = var.aurora_database_name
    AURORA_READER_USER      = var.aurora_reader_username
  })

  tags = {
    Name = "${local.name_prefix}-config"
  }
}
