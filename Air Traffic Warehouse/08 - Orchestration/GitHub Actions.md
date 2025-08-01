[« Back to Index](../00%20-%20Index.md)

# GitHub Actions for Orchestration

Instead of a heavy orchestrator like Airflow or Dagster, we use GitHub Actions to schedule and run our ETL pipeline.

## Why?
- **Zero Infrastructure**: No servers to manage.
- **Free**: Generous free tier for public repositories.
- **Simplicity**: Perfect for a batch pipeline running every 6 hours.

## Setup
We define a `.github/workflows/etl.yml` file that sets up Python, installs dependencies, and runs `make run`.

---
[« Back to Index](../00%20-%20Index.md)
