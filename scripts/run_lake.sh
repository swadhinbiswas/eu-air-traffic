#!/usr/bin/env bash
# One lake cycle: the same sequence .github/workflows/lake.yml runs, packaged
# for a Hugging Face Job (or any container). When the dataset is mounted
# locally, set HF_SYNC=0 and the HF pull/push round-trip is skipped entirely.
set -euo pipefail

export WAREHOUSE_TARGET="${WAREHOUSE_TARGET:-local}"
export DBT_TARGET="${DBT_TARGET:-dev}"
export AIR_TRAFFIC_DUCKDB_PATH="${AIR_TRAFFIC_DUCKDB_PATH:-$PWD/warehouse/air_traffic.duckdb}"
export LAKE_WINDOW_SECONDS="${LAKE_WINDOW_SECONDS:-420}"
HF_SYNC="${HF_SYNC:-1}"

if [ "$HF_SYNC" = "1" ]; then
  uv run python -m scripts.lake_sync pull-silver
fi

uv run python -m services.sink --window-seconds "$LAKE_WINDOW_SECONDS"
uv run python -m pipelines.silver

if [ "$HF_SYNC" = "1" ]; then
  uv run python -m scripts.lake_sync push-silver
fi

uv run python -m pipelines.warehouse
uv run dbt build --project-dir dbt --profiles-dir dbt
uv run python -m pipelines.warehouse --register-gold
uv run python -m scripts.publish_motherduck
uv run python -m pipelines.quality
uv run python -m scripts.publish_site_tables
uv run python -m scripts.publish_turso
