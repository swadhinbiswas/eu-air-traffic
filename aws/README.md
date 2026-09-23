# AWS version — application code

This directory holds the code for the **serverless analytics plane** shown in
[`images/aws-version.png`](../images/aws-version.png): the existing Kafka/DuckDB
pipeline lands raw Parquet in S3, Glue cleans it, and Athena queries the result.

The infrastructure that runs it is `infra/terraform-serverless/`. This directory
is only the code those services execute.

```
aws/
  lambda/bronze_export/handler.py   stage 1 — Lambda: lake → S3 raw Parquet
  glue/etl_job.py                   stage 4 — Glue PySpark: raw → curated
  athena/ddl/raw.sql                stage 6 — raw-zone table definitions
  athena/ddl/curated.sql            stage 6 — curated-zone table definitions
  athena/queries.sql                stage 7 — example BI queries
```

## How it fits the stages

| Stage | Service | Code |
|---|---|---|
| 1 Export raw data | AWS Lambda | `lambda/bronze_export/handler.py` |
| 2 Raw storage | Amazon S3 | — (layout only) |
| 3 Catalog | AWS Glue Crawler | — (Terraform) |
| 4 ETL / transform | AWS Glue (PySpark) | `glue/etl_job.py` |
| 5 Curated storage | Amazon S3 | — (layout only) |
| 6 Query | Amazon Athena | `athena/ddl/*.sql` |
| 7 Analytics / BI | QuickSight / Grafana | `athena/queries.sql` |

## Interface with the existing pipeline

The Lambda reads the lake the main pipeline already produces. Two sources are
supported, selected by environment:

- **S3** — set `SOURCE_BUCKET` (and optionally `SOURCE_PREFIX`). This is the
  path to use once the lake runs on S3 (`LAKE_BACKEND=s3`, see
  `services/lake_backend.py`).
- **Hugging Face** — set `HF_REPO` / `HF_TOKEN`. Used when the free lake is
  still a dataset repo.

It writes append-only objects:

```
s3://<raw-bucket>/<domain>/dt=YYYY-MM-DD/<domain>_<run>.parquet
```

so a re-run can never overwrite earlier data. Deduplication happens once, in the
Glue job, using the same business keys as the main pipeline
(`callsign + departure + arrival + date` for flights).

## Packaging

**Lambda.** `pyarrow` is the only third-party import. Either attach a layer that
provides it, or build a container image from `public.ecr.aws/lambda/python:3.12`:

```bash
cd aws/lambda/bronze_export
pip install pyarrow --target package/
cp handler.py package/
# build/push the container, or zip package/ for a layer
```

**Glue.** `etl_job.py` runs on the Glue 4.0 PySpark runtime, which already ships
`pyspark` and `awsglue`. Terraform uploads it to S3 and points the job at it, so
there is nothing to package.

## Local checks

```bash
# Lambda: pure-python part runs without AWS
python aws/lambda/bronze_export/handler.py

# Glue: syntax only (pyspark/awsglue are not installed locally)
python -m py_compile aws/glue/etl_job.py
```

Both are also covered by `ruff` and `ruff format`, like the rest of the repo.

## Notes

- This stack is a **reference implementation** and is not currently deployed;
  the live pipeline runs on free tiers. See `docs/aws-version.md`.
- The curated zone is derived — it can be rebuilt from the raw zone at any time,
  and the raw zone from the existing lake.
