# ─────────────────────────────────────────────────────────────────────────────
# Outputs an operator (or a deploy workflow) needs to drive the stack after
# `apply`, plus the ready-to-paste Athena snippet.
# ─────────────────────────────────────────────────────────────────────────────

output "aws_region" {
  description = "Region the serverless stack was created in."
  value       = var.aws_region
}

output "name_prefix" {
  description = "Prefix used for every resource name."
  value       = local.name_prefix
}

# ── Stage 1: export Lambda ───────────────────────────────────────────────────

output "lambda_function_name" {
  description = "Bronze export Lambda name. Invoke with `aws lambda invoke --function-name <name> out.json`."
  value       = aws_lambda_function.bronze_export.function_name
}

output "lambda_function_arn" {
  description = "Bronze export Lambda ARN."
  value       = aws_lambda_function.bronze_export.arn
}

output "lambda_schedule_name" {
  description = "EventBridge Scheduler schedule name, or null when scheduling is disabled."
  value       = one(aws_scheduler_schedule.bronze_export[*].name)
}

# ── Stage 2: raw zone ────────────────────────────────────────────────────────

output "raw_bucket_name" {
  description = "Raw/landing bucket (adsb/, opensky/, airlabs/, metar/, eurostat/)."
  value       = aws_s3_bucket.raw.bucket
}

output "raw_bucket_arn" {
  description = "Raw/landing bucket ARN."
  value       = aws_s3_bucket.raw.arn
}

# ── Stage 3: catalog ─────────────────────────────────────────────────────────

output "glue_crawler_name" {
  description = "Raw-zone Glue crawler name. Start it manually with `aws glue start-crawler --name <name>`."
  value       = aws_glue_crawler.raw.name
}

output "glue_crawler_schedule" {
  description = "Cron expression the raw-zone crawler runs on."
  value       = aws_glue_crawler.raw.schedule
}

output "glue_raw_database_name" {
  description = "Glue/Athena database holding crawled raw tables."
  value       = aws_glue_catalog_database.raw.name
}

# ── Stage 4: transform ───────────────────────────────────────────────────────

output "glue_job_name" {
  description = "Glue ETL job name. Run it manually with `aws glue start-job-run --job-name <name>`."
  value       = aws_glue_job.transform.name
}

output "glue_trigger_name" {
  description = "Conditional Glue trigger that runs the job after a successful crawl."
  value       = aws_glue_trigger.crawler_to_job.name
}

output "glue_etl_script_s3_uri" {
  description = "S3 URI of the uploaded PySpark script the Glue job runs."
  value       = "s3://${aws_s3_bucket.artifacts.bucket}/${aws_s3_object.etl_script.key}"
}

# ── Stage 5: curated zone ────────────────────────────────────────────────────

output "curated_bucket_name" {
  description = "Curated bucket (fact/, dim/, aggregates/, parquet/)."
  value       = aws_s3_bucket.curated.bucket
}

output "curated_bucket_arn" {
  description = "Curated bucket ARN."
  value       = aws_s3_bucket.curated.arn
}

output "glue_curated_database_name" {
  description = "Glue/Athena database holding curated fact/dim/aggregate tables."
  value       = aws_glue_catalog_database.curated.name
}

output "artifacts_bucket_name" {
  description = "Artifacts bucket holding the ETL script and Athena query results."
  value       = aws_s3_bucket.artifacts.bucket
}

# ── Stage 6: query ───────────────────────────────────────────────────────────

output "athena_workgroup_name" {
  description = "Athena workgroup BI tools should connect to (configuration enforced)."
  value       = aws_athena_workgroup.analytics.name
}

output "athena_results_location" {
  description = "S3 location where the workgroup writes query results."
  value       = local.athena_results_location
}

output "athena_database_name" {
  description = "Database to select in the Athena query editor (the curated Glue database)."
  value       = aws_glue_catalog_database.curated.name
}

output "athena_example_query" {
  description = "Ready-to-paste Athena SQL: discover curated tables, then query one."
  value       = local.athena_example_query
}

# ── Observability ────────────────────────────────────────────────────────────

output "sns_alerts_topic_arn" {
  description = "SNS topic that receives every alarm and Glue failure event."
  value       = aws_sns_topic.alerts.arn
}

output "lambda_log_group_name" {
  description = "CloudWatch log group for the export Lambda."
  value       = aws_cloudwatch_log_group.lambda.name
}

output "glue_job_log_group_name" {
  description = "CloudWatch log group for the Glue transform job."
  value       = aws_cloudwatch_log_group.glue_job.name
}
