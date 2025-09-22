# ─────────────────────────────────────────────────────────────────────────────
# ECS: Fargate cluster, the long-running collector service, and the lake task
# definition that Step Functions runs.
#
# ARM64 (Graviton) throughout. Both workloads are CPU-light and I/O-heavy, and
# the Dockerfiles already build cleanly for arm64 — roughly 20% cheaper for the
# same work (docs/aws-deployment.md §3).
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Environment variables the collector reads (config/settings.py). Secrets are
  # NOT here — they go in the container `secrets` block and are injected by the
  # execution role, so they never appear in the task definition in plaintext.
  collector_environment = {
    ENVIRONMENT             = var.environment
    LOG_LEVEL               = upper(var.log_level)
    MOCK_MODE               = var.mock_mode ? "true" : "false"
    AIVEN_KAFKA_HOST        = local.msk_bootstrap_brokers
    AIVEN_KAFKA_PORT        = tostring(local.msk_client_port)
    AIVEN_KAFKA_CA_CERT     = var.kafka_ca_cert_container_path
    KAFKA_SECURITY_PROTOCOL = "SASL_SSL"
    KAFKA_SASL_MECHANISM    = "SCRAM-SHA-512"
    KAFKA_TOPIC_POSITIONS   = var.kafka_topic_positions
    KAFKA_TOPIC_FLIGHTS     = var.kafka_topic_flights
    KAFKA_TOPIC_WEATHER     = var.kafka_topic_weather
    KAFKA_TOPIC_FUEL        = var.kafka_topic_fuel
    KAFKA_TOPIC_REFERENCE   = var.kafka_topic_reference
    KAFKA_RETENTION_HOURS   = tostring(var.kafka_retention_hours)
    LAKE_WINDOW_SECONDS     = tostring(var.lake_window_seconds)
    LIVE_API_HOST           = "0.0.0.0"
    LIVE_API_PORT           = tostring(var.collector_container_port)
    LIVE_API_PUBLIC_URL     = local.live_api_public_url
    AWS_REGION              = var.aws_region
    AWS_DEFAULT_REGION      = var.aws_region
  }

  # The lake job consumes Kafka (it does not produce), writes the S3 lake, runs
  # dbt against the warehouse and publishes the serving copy to Aurora. It does
  # not get the upstream API keys.
  lake_environment = {
    ENVIRONMENT             = var.environment
    LOG_LEVEL               = upper(var.log_level)
    MOCK_MODE               = var.mock_mode ? "true" : "false"
    AIVEN_KAFKA_HOST        = local.msk_bootstrap_brokers
    AIVEN_KAFKA_PORT        = tostring(local.msk_client_port)
    AIVEN_KAFKA_CA_CERT     = var.kafka_ca_cert_container_path
    KAFKA_SECURITY_PROTOCOL = "SASL_SSL"
    KAFKA_SASL_MECHANISM    = "SCRAM-SHA-512"
    KAFKA_TOPIC_POSITIONS   = var.kafka_topic_positions
    KAFKA_TOPIC_FLIGHTS     = var.kafka_topic_flights
    KAFKA_TOPIC_WEATHER     = var.kafka_topic_weather
    KAFKA_TOPIC_FUEL        = var.kafka_topic_fuel
    KAFKA_TOPIC_REFERENCE   = var.kafka_topic_reference
    SINK_CONSUMER_GROUP     = var.sink_consumer_group
    KAFKA_RETENTION_HOURS   = tostring(var.kafka_retention_hours)
    LAKE_WINDOW_SECONDS     = tostring(var.lake_window_seconds)
    LAKE_BACKEND            = "s3"
    S3_BUCKET               = aws_s3_bucket.lake.bucket
    S3_PREFIX               = var.s3_prefix
    AWS_REGION              = var.aws_region
    AWS_DEFAULT_REGION      = var.aws_region
    HF_REPO                 = var.hf_repo
    MOTHERDUCK_DATABASE     = var.motherduck_database
    # scripts/run_lake.sh dispatches on this: turso (default) | aurora | none.
    # AWS uses Aurora, so the Turso publisher is not run.
    SERVING_BACKEND = "aurora"
    # Emit the freshness metric the CloudWatch alarm watches (publish_metrics.py).
    LAKE_METRICS_ENABLED   = "true"
    LAKE_METRICS_NAMESPACE = var.lake_metrics_namespace
    # Aurora is the serving copy; the writer endpoint publishes, the reader
    # endpoint (and the read-only role) serves the dashboard. These are the
    # libpq variables psycopg reads directly; PGPASSWORD comes from the secret
    # block below, so no password is ever in the task definition.
    PGHOST     = aws_rds_cluster.aurora.endpoint
    PGPORT     = tostring(aws_rds_cluster.aurora.port)
    PGDATABASE = var.aurora_database_name
    PGUSER     = var.aurora_master_username
    PGSSLMODE  = "require"
  }
}

resource "aws_ecs_cluster" "main" {
  name = local.name_prefix

  # Container Insights is a small cost but the per-service CPU/memory graphs are
  # what make the ECS alarms actionable.
  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = local.name_prefix
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ── Collector task + service ─────────────────────────────────────────────────

resource "aws_ecs_task_definition" "collector" {
  family                   = "${local.name_prefix}-collector"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.collector_cpu
  memory                   = var.collector_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.collector_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "collector"
      image     = local.collector_image
      essential = true

      portMappings = [
        {
          containerPort = var.collector_container_port
          hostPort      = var.collector_container_port
          protocol      = "tcp"
        },
      ]

      environment = [
        for key, value in local.collector_environment : {
          name  = key
          value = value
        }
      ]

      secrets = [
        {
          name      = "OPENSKY_CLIENT_ID"
          valueFrom = "${aws_secretsmanager_secret.collector.arn}:OPENSKY_CLIENT_ID::"
        },
        {
          name      = "OPENSKY_CLIENT_SECRET"
          valueFrom = "${aws_secretsmanager_secret.collector.arn}:OPENSKY_CLIENT_SECRET::"
        },
        {
          name      = "AIRLABS_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.collector.arn}:AIRLABS_API_KEY::"
        },
        {
          name      = "AIVEN_KAFKA_USERNAME"
          valueFrom = "${aws_secretsmanager_secret.msk_scram.arn}:username::"
        },
        {
          name      = "AIVEN_KAFKA_PASSWORD"
          valueFrom = "${aws_secretsmanager_secret.msk_scram.arn}:password::"
        },
      ]

      # The image already defines a Docker HEALTHCHECK; repeating it here lets
      # ECS replace a task whose HTTP server has wedged even if the process is up.
      healthCheck = {
        command     = ["CMD-SHELL", "curl -fsS http://127.0.0.1:${var.collector_container_port}/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 20
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.collector.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "collector"
        }
      }
    },
  ])

  tags = {
    Name     = "${local.name_prefix}-collector"
    Workload = "collector"
  }
}

resource "aws_ecs_service" "collector" {
  name            = "${local.name_prefix}-collector"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.collector.arn
  desired_count   = var.collector_desired_count
  launch_type     = "FARGATE"

  # ARM64 requires Fargate platform 1.4.0.
  platform_version = "1.4.0"

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.collector.arn
    container_name   = "collector"
    container_port   = var.collector_container_port
  }

  health_check_grace_period_seconds = 60

  # Roll back automatically if the new task set does not become healthy.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # The task cannot start until the execution role can read the injected
  # secrets; without this Terraform may create the service first and the task
  # fails with a ResourceInitializationError.
  depends_on = [
    aws_iam_role_policy.ecs_execution_secrets,
    aws_iam_role_policy.collector_task,
  ]

  tags = {
    Name     = "${local.name_prefix}-collector"
    Workload = "collector"
  }
}

# ── Lake task definition (run by Step Functions, not a service) ─────────────
# There is deliberately no aws_ecs_service for the lake job. DuckDB and Iceberg
# both assume a single writer per table, so the task is invoked on demand by the
# state machine, which takes a lock before every run (stepfunctions.tf).

resource "aws_ecs_task_definition" "lake" {
  family                   = "${local.name_prefix}-lake"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.lake_cpu
  memory                   = var.lake_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.lake_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # No command/entrypoint override: docker/lake-job.Dockerfile already sets
  # ENTRYPOINT ["bash", "scripts/run_lake.sh"].
  container_definitions = jsonencode([
    {
      name      = "lake"
      image     = local.lake_image
      essential = true

      environment = [
        for key, value in local.lake_environment : {
          name  = key
          value = value
        }
      ]

      secrets = [
        {
          name      = "HF_TOKEN"
          valueFrom = "${aws_secretsmanager_secret.lake.arn}:HF_TOKEN::"
        },
        {
          name      = "MOTHERDUCK_TOKEN"
          valueFrom = "${aws_secretsmanager_secret.lake.arn}:MOTHERDUCK_TOKEN::"
        },
        {
          name      = "TURSO_TARGETS"
          valueFrom = "${aws_secretsmanager_secret.lake.arn}:TURSO_TARGETS::"
        },
        {
          name      = "AIVEN_KAFKA_USERNAME"
          valueFrom = "${aws_secretsmanager_secret.msk_scram.arn}:username::"
        },
        {
          name      = "AIVEN_KAFKA_PASSWORD"
          valueFrom = "${aws_secretsmanager_secret.msk_scram.arn}:password::"
        },
        {
          # RDS manages the master password; the execution role reads it from
          # the secret RDS created when manage_master_user_password was enabled.
          name      = "PGPASSWORD"
          valueFrom = "${aws_rds_cluster.aurora.master_user_secret[0].secret_arn}:password::"
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.lake.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "lake"
        }
      }
    },
  ])

  tags = {
    Name     = "${local.name_prefix}-lake"
    Workload = "lake"
  }
}
