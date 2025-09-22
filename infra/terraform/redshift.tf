# ─────────────────────────────────────────────────────────────────────────────
# Warehouse: Redshift Serverless.
#
# dbt targets this once the models are ported (dbt-redshift); the connection
# details are kept in Secrets Manager so nothing ends up in profiles.yml or a
# task definition. Toggle with var.enable_redshift for the cheaper
# S3 + Athena-only variant described in docs/aws-architecture.md §2.5.
# ─────────────────────────────────────────────────────────────────────────────

resource "random_password" "redshift" {
  count = var.enable_redshift ? 1 : 0

  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "redshift" {
  count = var.enable_redshift ? 1 : 0

  name                    = "${local.name_prefix}/redshift"
  description             = "Redshift Serverless admin credentials for dbt"
  recovery_window_in_days = var.secrets_recovery_window_days

  tags = {
    Workload = "warehouse"
  }
}

resource "aws_secretsmanager_secret_version" "redshift" {
  count = var.enable_redshift ? 1 : 0

  secret_id = aws_secretsmanager_secret.redshift[0].id

  secret_string = jsonencode({
    username = var.redshift_admin_username
    password = random_password.redshift[0].result
    host     = try(aws_redshiftserverless_workgroup.main[0].endpoint[0].address, "")
    port     = 5439
    database = var.redshift_database_name
  })
}

resource "aws_redshiftserverless_namespace" "main" {
  count = var.enable_redshift ? 1 : 0

  namespace_name      = local.name_prefix
  admin_username      = var.redshift_admin_username
  admin_user_password = random_password.redshift[0].result
  db_name             = var.redshift_database_name

  tags = {
    Name = local.name_prefix
  }
}

resource "aws_redshiftserverless_workgroup" "main" {
  count = var.enable_redshift ? 1 : 0

  namespace_name = aws_redshiftserverless_namespace.main[0].namespace_name
  workgroup_name = local.name_prefix

  base_capacity = var.redshift_base_capacity
  max_capacity  = var.redshift_max_capacity

  # Warehouse traffic stays inside the VPC; the workgroup is never public.
  publicly_accessible = false
  subnet_ids          = local.database_subnet_ids
  security_group_ids  = [aws_security_group.redshift.id]

  tags = {
    Name = local.name_prefix
  }

  lifecycle {
    precondition {
      condition     = var.az_count >= 3
      error_message = "Redshift Serverless requires subnets in at least three AZs; set az_count >= 3 or enable_redshift = false."
    }
  }
}
