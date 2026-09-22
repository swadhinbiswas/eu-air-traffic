# ─────────────────────────────────────────────────────────────────────────────
# MSK: the event bus.
#
# Provisioned MSK (not Serverless) with SASL/SCRAM over TLS. The app already
# speaks SASL_SSL + SCRAM-SHA-512 through kafka-python-ng, so this is a
# configuration change, not a code change. IAM auth would need the extra
# aws-msk-iam-sasl-signer plugin and a custom SASL mechanism in
# services/kafka_bus.py — deliberately avoided. See docs/aws-deployment.md §2.2.
#
# Topics are NOT created here: the AWS provider has no aws_msk_topic resource.
# Create them once after the cluster is up with the repo's admin tool:
#
#   AIVEN_KAFKA_HOST=<bootstrap> AIVEN_KAFKA_USERNAME=<user> \
#   AIVEN_KAFKA_PASSWORD=<pw> AIVEN_KAFKA_CA_CERT=deploy/aws-msk-ca.pem \
#   KAFKA_SECURITY_PROTOCOL=SASL_SSL KAFKA_SASL_MECHANISM=SCRAM-SHA-512 \
#     python -m scripts.kafka_admin create-topics --partitions 6
#
# That reads the same five topic names configured below, so the Terraform and
# the runtime agree by construction.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_msk_configuration" "main" {
  name           = "${local.name_prefix}-defaults"
  description    = "Broker defaults: no auto-create, replication matching broker count, 24h retention"
  kafka_versions = [var.msk_kafka_version]

  # default.replication.factor / min.insync.replicas must not exceed the broker
  # count, so a 1-broker sandbox gets a valid (if non-HA) config instead of a
  # failed create. Built with join() rather than a heredoc so no indentation
  # ends up in the properties.
  server_properties = join("\n", [
    "auto.create.topics.enable=false",
    "default.replication.factor=${local.msk_replication_factor}",
    "min.insync.replicas=${local.msk_min_insync_replicas}",
    "num.partitions=6",
    "offsets.topic.replication.factor=${local.msk_replication_factor}",
    "transaction.state.log.replication.factor=${local.msk_replication_factor}",
    "transaction.state.log.min.isr=${local.msk_min_insync_replicas}",
    "log.retention.hours=${var.kafka_retention_hours}",
    "log.cleanup.policy=delete",
  ])
}

resource "aws_msk_cluster" "main" {
  cluster_name           = local.name_prefix
  kafka_version          = var.msk_kafka_version
  number_of_broker_nodes = var.msk_broker_count

  broker_node_group_info {
    instance_type   = var.msk_instance_type
    client_subnets  = local.private_subnet_ids
    security_groups = [aws_security_group.msk.id]

    storage_info {
      ebs_storage_info {
        volume_size = var.msk_ebs_volume_size
      }
    }
  }

  # Client traffic is TLS-only; SCRAM credentials ride inside TLS.
  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  client_authentication {
    sasl {
      scram = true
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.main.arn
    revision = aws_msk_configuration.main.latest_revision
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.msk.name
      }
    }
  }

  open_monitoring {
    prometheus {
      jmx_exporter {
        enabled_in_broker = var.enable_msk_prometheus
      }
      node_exporter {
        enabled_in_broker = var.enable_msk_prometheus
      }
    }
  }

  tags = {
    Name = local.name_prefix
  }

  lifecycle {
    precondition {
      condition     = var.msk_broker_count % var.az_count == 0
      error_message = "MSK requires number_of_broker_nodes to be a multiple of the number of client subnets (az_count)."
    }
  }
}

# MSK's SCRAM secret must live in Secrets Manager and be registered against the
# cluster; this is the authoritative Kafka credential store. Both the collector
# and the lake task read username/password from it, so there is exactly one copy
# to rotate.
resource "random_password" "msk_scram" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "msk_scram" {
  name                    = "${local.name_prefix}/msk-scram"
  description             = "MSK SASL/SCRAM credentials for the collector and lake tasks"
  recovery_window_in_days = var.secrets_recovery_window_days

  tags = {
    Workload = "kafka"
  }
}

resource "aws_secretsmanager_secret_version" "msk_scram" {
  secret_id = aws_secretsmanager_secret.msk_scram.id

  secret_string = jsonencode({
    username = var.msk_scram_username
    password = random_password.msk_scram.result
  })
}

resource "aws_msk_scram_secret_association" "main" {
  cluster_arn = aws_msk_cluster.main.arn

  secret_arn_list = [aws_secretsmanager_secret.msk_scram.arn]

  # The cluster must be able to read the credentials as soon as it associates
  # them, so wait for the initial secret version.
  depends_on = [aws_secretsmanager_secret_version.msk_scram]
}
