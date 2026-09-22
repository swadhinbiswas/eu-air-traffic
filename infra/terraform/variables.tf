# ─────────────────────────────────────────────────────────────────────────────
# Inputs. Defaults are chosen so that a fresh `terraform apply` produces a
# complete, working (if deliberately small) stack; the values that genuinely
# must differ per account are the ones with no sensible default.
# ─────────────────────────────────────────────────────────────────────────────

# ── Identity / tagging ───────────────────────────────────────────────────────

variable "name_prefix" {
  description = "Prefix for every resource name and tag. Keep it short: ECS/ALB names are length-limited."
  type        = string
  default     = "eu-air-traffic"
}

variable "environment" {
  description = "Deployment environment, used in tags and as the app's ENVIRONMENT variable."
  type        = string
  default     = "production"

  validation {
    condition     = contains(["development", "test", "production"], var.environment)
    error_message = "environment must be one of: development, test, production."
  }
}

variable "aws_region" {
  description = "AWS region for the whole stack. MSK, Aurora, Redshift and CloudFront's S3 origin all live here."
  type        = string
  default     = "eu-central-1"
}

variable "tags" {
  description = "Extra tags merged into every resource alongside Project/Environment/ManagedBy."
  type        = map(string)
  default     = {}
}

# ── Networking ───────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Splits into public/private/database /24s per AZ."
  type        = string
  default     = "10.42.0.0/16"
}

variable "az_count" {
  description = "Number of Availability Zones to spread subnets across. MSK wants at least two; three is the sane default."
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 6
    error_message = "az_count must be between 2 and 6."
  }
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway for all private subnets (cheaper) instead of one per AZ (HA). Set false in production."
  type        = bool
  default     = true
}

variable "enable_interface_endpoints" {
  description = "Create Interface VPC endpoints for ECR/Secrets Manager/Logs. Optional: NAT already provides the path, endpoints cut NAT data charges at the cost of ~$7/endpoint/AZ."
  type        = bool
  default     = false
}

variable "kafka_ca_cert_container_path" {
  description = "Path inside the container to the MSK CA bundle (AmazonRootCA1.pem). Baked into the image by the Docker build."
  type        = string
  default     = "/app/deploy/aws-msk-ca.pem"
}

# ── Application runtime ──────────────────────────────────────────────────────

variable "log_level" {
  description = "Application log level (DEBUG/INFO/WARNING/ERROR)."
  type        = string
  default     = "INFO"
}

variable "mock_mode" {
  description = "Run collectors with synthetic data when upstream credentials are absent. Leave false in production."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for ECS and MSK log groups."
  type        = number
  default     = 30
}

# ── Container images ─────────────────────────────────────────────────────────

variable "image_tag" {
  description = "Tag used for both ECR images when collector_image/lake_image are left empty. CI pushes the short git SHA."
  type        = string
  default     = "latest"
}

variable "collector_image" {
  description = "Full collector image URI. Empty means <ecr-repo>:<image_tag>."
  type        = string
  default     = ""
}

variable "lake_image" {
  description = "Full lake-job image URI. Empty means <ecr-repo>:<image_tag>."
  type        = string
  default     = ""
}

variable "ecr_force_delete" {
  description = "Allow ECR repositories to be deleted even if they still contain images."
  type        = bool
  default     = false
}

variable "ecr_untagged_expiry_days" {
  description = "Days after which untagged ECR images are expired."
  type        = number
  default     = 14
}

variable "ecr_keep_image_count" {
  description = "Number of most-recent images to retain in each ECR repository."
  type        = number
  default     = 20
}

# ── Collector service ────────────────────────────────────────────────────────

variable "collector_desired_count" {
  description = "Number of collector Fargate tasks. Start at 1; the live snapshot is in-memory, so >1 needs shared state."
  type        = number
  default     = 1
}

variable "collector_cpu" {
  description = "Collector task CPU units (Fargate, 1024 = 1 vCPU)."
  type        = number
  default     = 1024
}

variable "collector_memory" {
  description = "Collector task memory in MiB."
  type        = number
  default     = 2048
}

variable "collector_container_port" {
  description = "Port the collector's live API listens on. Matches docker/Dockerfile.collector EXPOSE."
  type        = number
  default     = 8090
}

variable "lake_cpu" {
  description = "Lake task CPU units. The batch job is CPU- and I/O-heavy while it builds DuckDB + dbt."
  type        = number
  default     = 2048
}

variable "lake_memory" {
  description = "Lake task memory in MiB."
  type        = number
  default     = 4096
}

# ── Kafka / MSK ──────────────────────────────────────────────────────────────

variable "kafka_topic_positions" {
  description = "Topic for live positions (kept at the app default)."
  type        = string
  default     = "eu-positions"
}

variable "kafka_topic_flights" {
  description = "Topic for movements/schedules (kept at the app default)."
  type        = string
  default     = "eu-flights"
}

variable "kafka_topic_weather" {
  description = "Topic for metar/taf/forecast, split downstream by the _kind discriminator."
  type        = string
  default     = "eu-weather"
}

variable "kafka_topic_fuel" {
  description = "Topic for fuel prices."
  type        = string
  default     = "eu-fuel"
}

variable "kafka_topic_reference" {
  description = "Topic for reference data."
  type        = string
  default     = "eu-reference"
}

variable "sink_consumer_group" {
  description = "Consumer group the lake sink reads under."
  type        = string
  default     = "eu-air-traffic-sink"
}

variable "lake_window_seconds" {
  description = "How far back each lake cycle drains Kafka, in seconds. Must stay well under Kafka retention."
  type        = number
  default     = 540
}

variable "kafka_retention_hours" {
  description = "Kafka topic retention in hours. Must exceed the scheduler cadence or records are lost between cycles."
  type        = number
  default     = 24
}

variable "msk_kafka_version" {
  description = "MSK Kafka version."
  type        = string
  default     = "3.6.0"
}

variable "msk_instance_type" {
  description = "MSK broker instance type. kafka.t3.small is the cheap end; use kafka.m5.large or better for real load."
  type        = string
  default     = "kafka.t3.small"
}

variable "msk_broker_count" {
  description = "Number of MSK brokers. Must equal the number of private subnets used."
  type        = number
  default     = 3
}

variable "msk_ebs_volume_size" {
  description = "EBS volume size (GiB) per MSK broker."
  type        = number
  default     = 100
}

variable "msk_scram_username" {
  description = "Username for the MSK SASL/SCRAM credentials. The password is generated and stored in Secrets Manager."
  type        = string
  default     = "msk-scram-user"
}

variable "enable_msk_prometheus" {
  description = "Enable MSK open monitoring (Prometheus JMX/node exporters). Useful with Managed Prometheus; costs extra."
  type        = bool
  default     = false
}

# ── S3 lake / web ────────────────────────────────────────────────────────────

variable "lake_bucket_name" {
  description = "Explicit lake bucket name. Empty derives <name_prefix>-lake-<account_id> to stay globally unique."
  type        = string
  default     = ""
}

variable "web_bucket_name" {
  description = "Explicit dashboard bucket name. Empty derives <name_prefix>-web-<account_id>."
  type        = string
  default     = ""
}

variable "lake_bucket_force_destroy" {
  description = "Allow the lake bucket to be destroyed with objects in it. Never true for a bucket you care about."
  type        = bool
  default     = false
}

variable "web_bucket_force_destroy" {
  description = "Allow the web bucket to be destroyed with objects in it."
  type        = bool
  default     = true
}

variable "s3_prefix" {
  description = "Prefix under the lake bucket for Bronze/Silver/Gold. Empty matches docs/aws-deployment.md (bronze/... at the root)."
  type        = string
  default     = ""
}

variable "bronze_ia_transition_days" {
  description = "Days before Bronze objects move to STANDARD_IA."
  type        = number
  default     = 30
}

variable "bronze_glacier_transition_days" {
  description = "Days before Bronze objects move to Glacier Instant Retrieval."
  type        = number
  default     = 90
}

variable "noncurrent_version_expiration_days" {
  description = "Days before noncurrent object versions are deleted."
  type        = number
  default     = 365
}

variable "glue_database_name" {
  description = "Glue Data Catalog database name for the lake tables."
  type        = string
  default     = "eu_air_traffic"
}

# ── Secrets ──────────────────────────────────────────────────────────────────

variable "secrets_recovery_window_days" {
  description = "Secrets Manager recovery window. 0 deletes immediately (only useful for throwaway stacks)."
  type        = number
  default     = 7
}

variable "hf_repo" {
  description = "Hugging Face repo id. Keep it configured while the lake backend optionally mirrors to HF; ignored for pure S3 runs."
  type        = string
  default     = "swadhinbiswas/air-traffic"
}

variable "motherduck_database" {
  description = "MotherDuck database name used as the warehouse while the dbt target has not been moved to Redshift."
  type        = string
  default     = "air_traffic"
}

# ── ALB / TLS ────────────────────────────────────────────────────────────────

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the live API on port 443 (same region). Empty leaves the ALB on HTTP only."
  type        = string
  default     = ""
}

variable "live_api_public_url" {
  description = "Public base URL baked into LIVE_API_PUBLIC_URL and the dashboard build. Empty derives it from the ALB."
  type        = string
  default     = ""
}

variable "alb_deletion_protection" {
  description = "Enable ALB deletion protection."
  type        = bool
  default     = false
}

variable "alb_ssl_policy" {
  description = "ALB TLS security policy for the HTTPS listener."
  type        = string
  default     = "ELBSecurityPolicy-TLS13-1-2-2021-06"
}

# ── Step Functions / scheduler ───────────────────────────────────────────────

variable "enable_lake_schedule" {
  description = "Create the EventBridge Scheduler rule that runs the lake cycle every 15 minutes."
  type        = bool
  default     = true
}

# ── Warehouse: Redshift Serverless ───────────────────────────────────────────

variable "enable_redshift" {
  description = "Provision Redshift Serverless as the warehouse. Turn off to keep the lake + Athena-only variant."
  type        = bool
  default     = true
}

variable "redshift_admin_username" {
  description = "Redshift Serverless admin username."
  type        = string
  default     = "admin"
}

variable "redshift_database_name" {
  description = "Redshift Serverless database name."
  type        = string
  default     = "air_traffic"
}

variable "redshift_base_capacity" {
  description = "Redshift Serverless base RPU capacity (8 is the minimum)."
  type        = number
  default     = 8
}

variable "redshift_max_capacity" {
  description = "Redshift Serverless maximum RPU capacity."
  type        = number
  default     = 64
}

# ── Serving: Aurora PostgreSQL Serverless v2 ─────────────────────────────────

variable "aurora_engine_version" {
  description = "Aurora PostgreSQL engine version. Empty lets AWS pick the current default."
  type        = string
  default     = ""
}

variable "aurora_master_username" {
  description = "Aurora master username. The password is managed by RDS in Secrets Manager (manage_master_user_password)."
  type        = string
  default     = "airtraffic_admin"
}

variable "aurora_database_name" {
  description = "Aurora database name for the serving copy."
  type        = string
  default     = "air_traffic"
}

variable "aurora_min_acu" {
  description = "Aurora Serverless v2 minimum ACUs. 0.5 is the floor; 0 is not supported for Provisioned+Serverless v2."
  type        = number
  default     = 0.5
}

variable "aurora_max_acu" {
  description = "Aurora Serverless v2 maximum ACUs."
  type        = number
  default     = 4
}

variable "aurora_reader_count" {
  description = "Number of Aurora reader instances. The reader endpoint backs the dashboard."
  type        = number
  default     = 1
}

variable "aurora_backup_retention_days" {
  description = "Aurora automated backup retention in days."
  type        = number
  default     = 7
}

variable "aurora_deletion_protection" {
  description = "Enable Aurora deletion protection."
  type        = bool
  default     = false
}

variable "aurora_skip_final_snapshot" {
  description = "Skip the final snapshot when the Aurora cluster is destroyed."
  type        = bool
  default     = false
}

variable "aurora_reader_username" {
  description = "Read-only database role used by the dashboard serving path. Not the master user."
  type        = string
  default     = "dashboard_reader"
}

variable "manage_aurora_reader_role" {
  description = "Run the opt-in provisioner that creates the read-only Postgres role. Needs psql and network access from the Terraform runner; otherwise create it by hand (see README)."
  type        = bool
  default     = false
}

# ── Dashboard: CloudFront ────────────────────────────────────────────────────

variable "cloudfront_acm_certificate_arn" {
  description = "ACM certificate ARN for the dashboard custom domain. MUST be in us-east-1 for CloudFront. Empty uses the default *.cloudfront.net certificate."
  type        = string
  default     = ""
}

variable "cloudfront_aliases" {
  description = "Custom domain aliases for the dashboard distribution. Only valid when cloudfront_acm_certificate_arn is set."
  type        = list(string)
  default     = []
}

variable "cloudfront_price_class" {
  description = "CloudFront price class. PriceClass_100 (US/EU) is the cheap default for a European audience."
  type        = string
  default     = "PriceClass_100"
}

# ── Observability ────────────────────────────────────────────────────────────

variable "alarm_email" {
  description = "Email address subscribed to the alerts SNS topic. Empty creates the topic with no subscription."
  type        = string
  default     = ""
}

variable "collector_cpu_alarm_threshold" {
  description = "CPU percentage that raises the collector saturation alarm."
  type        = number
  default     = 85
}

variable "msk_consumer_lag_threshold" {
  description = "Consumer offset lag on the sink group that raises the MSK lag alarm."
  type        = number
  default     = 1000000
}

variable "lake_metrics_namespace" {
  description = "CloudWatch namespace the pipeline report writes custom lake metrics to (e.g. scripts/write_pipeline_report.py)."
  type        = string
  default     = "EUAirTraffic/Lake"
}

variable "lake_freshness_metric_name" {
  description = "Custom metric name carrying the age of the last successful lake cycle, in seconds."
  type        = string
  default     = "LastSuccessfulCycleAgeSeconds"
}

variable "lake_freshness_threshold_seconds" {
  description = "Age of the last successful lake cycle that counts as stale. Default is twice the 15-minute cadence."
  type        = number
  default     = 1800
}

# ── CI/CD: GitHub Actions OIDC ───────────────────────────────────────────────

variable "github_repository" {
  description = "GitHub repo (owner/name) allowed to assume the deploy role."
  type        = string
  default     = "swadhinbiswas/eu-air-traffic"
}

variable "github_deploy_branch" {
  description = "Branch allowed to assume the deploy role. Keep this main; the trust policy matches only this ref."
  type        = string
  default     = "main"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub Actions IAM OIDC provider. Set false if the account already has one and pass github_oidc_provider_arn instead."
  type        = bool
  default     = true
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN. Only used when create_github_oidc_provider is false."
  type        = string
  default     = ""
}

variable "github_oidc_thumbprint" {
  description = "Thumbprint for the GitHub OIDC provider. AWS now trusts GitHub's CA, but the argument is still required; the value is not security-critical on current AWS."
  type        = string
  default     = "6938fd4d98bab03faadb97b34396831e3780aea1"
}
