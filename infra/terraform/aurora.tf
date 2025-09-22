# ─────────────────────────────────────────────────────────────────────────────
# Serving layer: Aurora PostgreSQL Serverless v2.
#
# Why this shape:
#   * Serverless v2 (engine_mode "provisioned" + serverlessv2_scaling_configuration)
#     scales down towards zero when the dashboard is idle — the whole point of
#     the serving copy is that it is a derived artefact, not a hot system.
#   * A writer endpoint for the lake's publish step and a reader endpoint for
#     the dashboard, preserving today's MotherDuck-vs-Turso split.
#   * IAM database authentication so the serving path can use short-lived
#     credentials instead of a static password (docs/aws-deployment.md §9.1).
#   * The master password is generated and owned by RDS in Secrets Manager
#     (manage_master_user_password), so it never appears in this repo.
#
# The read-only Postgres role cannot be created by any AWS provider resource —
# there is no such resource. It is created by an opt-in provisioner
# (var.manage_aurora_reader_role) or by hand; see the README.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "aurora" {
  name       = "${local.name_prefix}-aurora"
  subnet_ids = local.database_subnet_ids

  tags = {
    Name = "${local.name_prefix}-aurora"
  }
}

resource "aws_rds_cluster" "aurora" {
  cluster_identifier = "${local.name_prefix}-aurora"
  engine             = "aurora-postgresql"

  # Serverless *v2* is not engine_mode "serverless" (that was v1). It is a
  # provisioned cluster whose instances are db.serverless and whose capacity is
  # governed by serverlessv2_scaling_configuration below.
  engine_mode    = "provisioned"
  engine_version = var.aurora_engine_version != "" ? var.aurora_engine_version : null

  database_name = var.aurora_database_name

  # RDS creates and rotates the master secret in Secrets Manager.
  master_username             = var.aurora_master_username
  manage_master_user_password = true

  iam_database_authentication_enabled = true

  db_subnet_group_name   = aws_db_subnet_group.aurora.name
  vpc_security_group_ids = [aws_security_group.aurora.id]

  storage_encrypted = true

  backup_retention_period = var.aurora_backup_retention_days
  preferred_backup_window = "02:00-03:00"
  copy_tags_to_snapshot   = true

  deletion_protection       = var.aurora_deletion_protection
  skip_final_snapshot       = var.aurora_skip_final_snapshot
  final_snapshot_identifier = var.aurora_skip_final_snapshot ? null : "${local.name_prefix}-aurora-final"

  enabled_cloudwatch_logs_exports = ["postgresql"]

  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_min_acu
    max_capacity = var.aurora_max_acu
  }

  tags = {
    Name = "${local.name_prefix}-aurora"
  }
}

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "${local.name_prefix}-aurora-writer"
  cluster_identifier = aws_rds_cluster.aurora.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.aurora.engine

  publicly_accessible  = false
  db_subnet_group_name = aws_db_subnet_group.aurora.name
  promotion_tier       = 0

  tags = {
    Name = "${local.name_prefix}-aurora-writer"
    Role = "writer"
  }
}

# At least one reader gives the cluster a reader endpoint for the dashboard.
resource "aws_rds_cluster_instance" "reader" {
  count = var.aurora_reader_count

  identifier         = "${local.name_prefix}-aurora-reader-${count.index}"
  cluster_identifier = aws_rds_cluster.aurora.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.aurora.engine

  publicly_accessible  = false
  db_subnet_group_name = aws_db_subnet_group.aurora.name
  promotion_tier       = 1

  tags = {
    Name = "${local.name_prefix}-aurora-reader-${count.index}"
    Role = "reader"
  }
}

# ── Read-only role for the dashboard serving path ────────────────────────────

resource "random_password" "aurora_reader" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "aurora_reader" {
  name                    = "${local.name_prefix}/aurora-reader"
  description             = "Read-only Aurora credentials for the dashboard serving path"
  recovery_window_in_days = var.secrets_recovery_window_days

  tags = {
    Workload = "serving"
  }
}

resource "aws_secretsmanager_secret_version" "aurora_reader" {
  secret_id = aws_secretsmanager_secret.aurora_reader.id

  secret_string = jsonencode({
    username = var.aurora_reader_username
    password = random_password.aurora_reader.result
    host     = aws_rds_cluster.aurora.reader_endpoint
    port     = aws_rds_cluster.aurora.port
    database = var.aurora_database_name
  })
}

# Reading the RDS-managed master secret so the provisioner can authenticate.
# Only created when the opt-in provisioner is enabled.
data "aws_secretsmanager_secret_version" "aurora_master" {
  count = var.manage_aurora_reader_role ? 1 : 0

  secret_id = aws_rds_cluster.aurora.master_user_secret[0].secret_arn
}

# Opt-in: create the read-only Postgres role and its grants. There is no AWS
# provider resource for database roles, so this shells out to psql. It needs
# psql on the machine running Terraform and a network path to Aurora. Leave
# var.manage_aurora_reader_role = false (the default) and run the same SQL by
# hand from a bastion if you would rather not grant Terraform that path.
resource "null_resource" "aurora_reader_role" {
  count = var.manage_aurora_reader_role ? 1 : 0

  triggers = {
    cluster_identifier = aws_rds_cluster.aurora.id
    reader_username    = var.aurora_reader_username
    password_hash      = sha256(random_password.aurora_reader.result)
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]

    environment = {
      PGHOST          = aws_rds_cluster.aurora.endpoint
      PGPORT          = tostring(aws_rds_cluster.aurora.port)
      PGDATABASE      = var.aurora_database_name
      PGUSER          = var.aurora_master_username
      PGPASSWORD      = jsondecode(data.aws_secretsmanager_secret_version.aurora_master[0].secret_string)["password"]
      PGSSLMODE       = "require"
      READER_USER     = var.aurora_reader_username
      READER_PASSWORD = random_password.aurora_reader.result
    }

    command = <<-EOT
      set -euo pipefail

      EXISTS=$(psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$READER_USER'")
      if [ "$EXISTS" != "1" ]; then
        psql -v ON_ERROR_STOP=1 -c "CREATE ROLE \"$READER_USER\" LOGIN PASSWORD '$READER_PASSWORD'"
      else
        psql -v ON_ERROR_STOP=1 -c "ALTER ROLE \"$READER_USER\" PASSWORD '$READER_PASSWORD'"
      fi

      psql -v ON_ERROR_STOP=1 -c "GRANT CONNECT ON DATABASE \"$PGDATABASE\" TO \"$READER_USER\""
      psql -v ON_ERROR_STOP=1 -c "GRANT USAGE ON SCHEMA public TO \"$READER_USER\""
      psql -v ON_ERROR_STOP=1 -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO \"$READER_USER\""
      psql -v ON_ERROR_STOP=1 -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO \"$READER_USER\""
    EOT
  }

  depends_on = [aws_rds_cluster_instance.writer]
}
