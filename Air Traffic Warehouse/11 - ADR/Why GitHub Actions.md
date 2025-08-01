[« Back to Index](../00%20-%20Index.md)

# ADR: Why GitHub Actions?

**Context**: We need an orchestrator to schedule our ETL pipeline.

**Options Considered**: Apache Airflow, Dagster, Prefect, GitHub Actions.

**Decision**: GitHub Actions.

**Rationale**:
- Airflow/Dagster require dedicated infrastructure to run continuously.
- GitHub Actions is free, already integrated with our code repository, and fully capable of running cron-based batch jobs. This keeps the architecture serverless and zero-cost.

---
[« Back to Index](../00%20-%20Index.md)
