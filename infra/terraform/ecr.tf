# ─────────────────────────────────────────────────────────────────────────────
# ECR: two repositories, one per image built by CI.
#
#   docker/Dockerfile.collector  -> <name_prefix>/collector   (ARM64, port 8090)
#   docker/lake-job.Dockerfile   -> <name_prefix>/lake-job    (ARM64, entrypoint run_lake.sh)
#
# Scan-on-push catches the common base-image CVEs without a scheduled inspector;
# the lifecycle policy stops the repo growing without bound.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "collector" {
  name                 = "${local.name_prefix}/collector"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name     = "${local.name_prefix}/collector"
    Workload = "collector"
  }
}

resource "aws_ecr_repository" "lake" {
  name                 = "${local.name_prefix}/lake-job"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name     = "${local.name_prefix}/lake-job"
    Workload = "lake"
  }
}

resource "aws_ecr_lifecycle_policy" "collector" {
  repository = aws_ecr_repository.collector.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after ${var.ecr_untagged_expiry_days} days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.ecr_untagged_expiry_days
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep the ${var.ecr_keep_image_count} most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.ecr_keep_image_count
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}

resource "aws_ecr_lifecycle_policy" "lake" {
  repository = aws_ecr_repository.lake.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after ${var.ecr_untagged_expiry_days} days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.ecr_untagged_expiry_days
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep the ${var.ecr_keep_image_count} most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.ecr_keep_image_count
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}
