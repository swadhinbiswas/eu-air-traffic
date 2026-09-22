# ─────────────────────────────────────────────────────────────────────────────
# Glue Data Catalog: the metastore for Silver and Gold.
#
# The database is the durable part. The two crawlers are a convenience: once the
# schemas are stable, register the tables explicitly (or with Iceberg + the Glue
# catalog as the metastore) and drop the crawlers. They are here so a first
# deployment can query the lake with Athena without writing CREATE TABLE DDL.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_glue_catalog_database" "main" {
  name        = var.glue_database_name
  description = "EU Air Traffic lake catalog (Bronze/Silver/Gold Parquet)"
}

resource "aws_glue_crawler" "silver" {
  name          = "${local.name_prefix}-silver"
  description   = "Discover Silver Parquet under s3://<lake>/silver/"
  role          = aws_iam_role.glue.arn
  database_name = aws_glue_catalog_database.main.name
  table_prefix  = "silver_"

  s3_target {
    path = "s3://${aws_s3_bucket.lake.bucket}/silver/"
  }

  schema_change_policy {
    update_behavior = "LOG"
    delete_behavior = "LOG"
  }

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = {
        AddOrUpdateBehavior = "InheritFromTable"
      }
    }
    Grouping = {
      TableGroupingPolicy = "CombineCompatibleSchemas"
    }
  })

  tags = {
    Name = "${local.name_prefix}-silver-crawler"
  }
}

resource "aws_glue_crawler" "gold" {
  name          = "${local.name_prefix}-gold"
  description   = "Discover Gold marts under s3://<lake>/gold/"
  role          = aws_iam_role.glue.arn
  database_name = aws_glue_catalog_database.main.name
  table_prefix  = "gold_"

  s3_target {
    path = "s3://${aws_s3_bucket.lake.bucket}/gold/"
  }

  schema_change_policy {
    update_behavior = "LOG"
    delete_behavior = "LOG"
  }

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = {
        AddOrUpdateBehavior = "InheritFromTable"
      }
    }
    Grouping = {
      TableGroupingPolicy = "CombineCompatibleSchemas"
    }
  })

  tags = {
    Name = "${local.name_prefix}-gold-crawler"
  }
}
