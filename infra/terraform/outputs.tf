# ─────────────────────────────────────────────────────────────────────────────
# Outputs consumed by humans and by CI. Everything a deploy workflow or an
# operator needs to find the running stack lives here.
# ─────────────────────────────────────────────────────────────────────────────

output "aws_region" {
  description = "Region the stack was created in."
  value       = var.aws_region
}

output "name_prefix" {
  description = "Prefix used for every resource name."
  value       = local.name_prefix
}

output "vpc_id" {
  description = "VPC id."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnets used by ECS tasks and MSK."
  value       = local.private_subnet_ids
}

# ── ECS ──────────────────────────────────────────────────────────────────────

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.main.name
}

output "collector_service_name" {
  description = "ECS service name for the collector."
  value       = aws_ecs_service.collector.name
}

output "lake_task_definition_arn" {
  description = "Task definition ARN the lake state machine runs."
  value       = aws_ecs_task_definition.lake.arn
}

# ── Images ───────────────────────────────────────────────────────────────────

output "ecr_collector_repository_url" {
  description = "ECR repository URL for the collector image."
  value       = aws_ecr_repository.collector.repository_url
}

output "ecr_lake_repository_url" {
  description = "ECR repository URL for the lake-job image."
  value       = aws_ecr_repository.lake.repository_url
}

output "collector_image" {
  description = "Collector image currently referenced by the task definition."
  value       = local.collector_image
}

output "lake_image" {
  description = "Lake image currently referenced by the task definition."
  value       = local.lake_image
}

# ── Live API ─────────────────────────────────────────────────────────────────

output "alb_dns_name" {
  description = "ALB DNS name. Point a CNAME at this (or use the live url below)."
  value       = aws_lb.main.dns_name
}

output "live_api_url" {
  description = "Public base URL the dashboard calls for the live map."
  value       = local.live_api_public_url
}

# ── Storage ──────────────────────────────────────────────────────────────────

output "lake_bucket_name" {
  description = "S3 bucket holding Bronze/Silver/Gold and checkpoints."
  value       = aws_s3_bucket.lake.bucket
}

output "web_bucket_name" {
  description = "S3 bucket holding the dashboard build."
  value       = aws_s3_bucket.web.bucket
}

output "glue_database_name" {
  description = "Glue Data Catalog database for lake tables."
  value       = aws_glue_catalog_database.main.name
}

# ── Kafka ────────────────────────────────────────────────────────────────────

output "msk_cluster_arn" {
  description = "MSK cluster ARN."
  value       = aws_msk_cluster.main.arn
}

output "msk_bootstrap_brokers_sasl_scram" {
  description = "MSK bootstrap brokers for SASL/SCRAM over TLS. Use with port 9096."
  value       = aws_msk_cluster.main.bootstrap_brokers_sasl_scram
}

output "msk_scram_secret_arn" {
  description = "Secrets Manager secret holding the MSK SCRAM username/password."
  value       = aws_secretsmanager_secret.msk_scram.arn
}

# ── Orchestration ────────────────────────────────────────────────────────────

output "sfn_state_machine_arn" {
  description = "Lake Step Functions state machine ARN."
  value       = aws_sfn_state_machine.lake.arn
}

output "lake_schedule_name" {
  description = "EventBridge Scheduler rule name, or null when scheduling is disabled."
  value       = one(aws_scheduler_schedule.lake[*].name)
}

# ── Serving / warehouse ──────────────────────────────────────────────────────

output "aurora_writer_endpoint" {
  description = "Aurora writer endpoint (the lake publish path)."
  value       = aws_rds_cluster.aurora.endpoint
}

output "aurora_reader_endpoint" {
  description = "Aurora reader endpoint (the dashboard serving path)."
  value       = aws_rds_cluster.aurora.reader_endpoint
}

output "aurora_reader_secret_arn" {
  description = "Secrets Manager secret for the read-only Aurora role."
  value       = aws_secretsmanager_secret.aurora_reader.arn
}

output "aurora_master_secret_arn" {
  description = "Secrets Manager secret RDS created for the master user."
  value       = aws_rds_cluster.aurora.master_user_secret[0].secret_arn
}

output "redshift_workgroup_endpoint" {
  description = "Redshift Serverless workgroup endpoint address, or null when disabled."
  value       = try(aws_redshiftserverless_workgroup.main[0].endpoint[0].address, null)
}

# ── Dashboard ────────────────────────────────────────────────────────────────

output "cloudfront_domain_name" {
  description = "CloudFront domain serving the dashboard."
  value       = aws_cloudfront_distribution.web.domain_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (for cache invalidations)."
  value       = aws_cloudfront_distribution.web.id
}

# ── Alerts / secrets / CI ────────────────────────────────────────────────────

output "sns_alerts_topic_arn" {
  description = "SNS topic that receives every alarm."
  value       = aws_sns_topic.alerts.arn
}

output "collector_secret_arn" {
  description = "Secrets Manager secret for the collector's upstream API keys."
  value       = aws_secretsmanager_secret.collector.arn
}

output "lake_secret_arn" {
  description = "Secrets Manager secret for the lake's storage/warehouse/serving credentials."
  value       = aws_secretsmanager_secret.lake.arn
}

output "app_config_parameter_name" {
  description = "SSM parameter holding the non-secret runtime configuration."
  value       = aws_ssm_parameter.app_config.name
}

output "github_deploy_role_arn" {
  description = "IAM role GitHub Actions assumes through OIDC."
  value       = aws_iam_role.github_deploy.arn
}
