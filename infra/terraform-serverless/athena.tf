# ─────────────────────────────────────────────────────────────────────────────
# Stage 6 — Query (Amazon Athena).
#
# BI tools connect to the workgroup, not to a cluster. The curated tables are
# already in the Glue Data Catalog (written by the transform job), so there is
# no separate Athena database resource: in Athena, a database *is* a Glue
# database. `aws_glue_catalog_database.curated` is the database to point at.
#
# Workgroup configuration is enforced, which pins every client to this stack's
# result location, this stack's scan guard-rail and SSE-S3 on results. That is
# what makes the workgroup safe to hand to a dashboard team.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_athena_workgroup" "analytics" {
  name        = local.athena_workgroup_name
  description = "EU Air Traffic curated analytics (fact/dim/aggregate Parquet)"
  state       = "ENABLED"

  configuration {
    # Enforced means a client cannot override the result location or the scan
    # limit per query — the guard-rails below cannot be opted out of.
    enforce_workgroup_configuration = true

    # Publish per-query metrics (queries, bytes scanned) so the dashboard team
    # can see what it is spending, and so a cost alarm can be added later.
    publish_cloudwatch_metrics_enabled = true

    # The single most effective Athena cost control: refuse any query predicted
    # to scan more than this. Default 10 GiB.
    bytes_scanned_cutoff_per_query = var.athena_bytes_scanned_cutoff_per_query

    result_configuration {
      output_location = local.athena_results_location

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }

  tags = {
    Name  = local.athena_workgroup_name
    Stage = "6-query"
  }
}

# A named query so the first thing an analyst sees in the Athena console is a
# working example, already scoped to the right workgroup and database.
resource "aws_athena_named_query" "sample" {
  name        = "${local.name_prefix}-sample-curated-query"
  description = "List curated tables, then query one"
  workgroup   = aws_athena_workgroup.analytics.name
  database    = aws_glue_catalog_database.curated.name

  # A single statement: Athena executes one statement per query execution. The
  # richer two-part example is exposed through the `athena_example_query` output.
  query = local.athena_discovery_query
}
