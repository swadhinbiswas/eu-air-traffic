[« Back to Index](../00%20-%20Index.md)

# Goals

## Primary Objectives

1. **Build a Fully Open-Source Data Warehouse**
   - Collect, transform, store, and analyze global flight data without proprietary cloud services (no AWS Glue, S3, Redshift, or BigQuery)

2. **Demonstrate Production Engineering Practices**
   - Idempotent pipelines
   - Automated CI/CD
   - Comprehensive testing
   - Observability and logging
   - Containerization

3. **Achieve Zero-Cost Deployment**
   - Use Hugging Face Datasets for storage
   - GitHub Actions for orchestration
   - Free-tier hosting for dashboards

4. **Ensure Reproducibility**
   - A recruiter must be able to clone, bootstrap, and run the entire warehouse with two commands

## Business Goals

| Goal | Metric |
|------|--------|
| Airport delay analysis | Top 10 delayed airports identified |
| Airline performance ranking | Monthly performance scores |
| Weather impact studies | Correlation between weather events and delays |
| Seasonal trend detection | Peak travel periods identified |
| Route efficiency analysis | Most efficient routes surfaced |

## Technical Goals

| Goal | Success Criteria |
|------|-----------------|
| Query latency | Under 2 seconds for dashboard queries |
| Data freshness | Updated every 6 hours |
| Pipeline reliability | >99% successful runs |
| Code coverage | >80% test coverage |
| Data quality | All validation rules enforced |
| Storage efficiency | Parquet compression reducing size by >70% |

## Stretch Goals (Senior-Level)

- Change Data Capture (CDC) for real-time ingestion
- Kafka streaming ingestion
- Apache Iceberg table format
- Data lineage tracking
- Data catalog (Amundsen/DataHub)
- Query optimization engine
- Multi-region dataset partitioning
- ML-based delay prediction
- Feature store for ML models
- Streaming analytics with real-time dashboards

## Non-Goals

- This is NOT a commercial product
- This does NOT use managed cloud services (by design)
- This is NOT real-time (batch processing, every 6 hours)
- This is NOT a data science project (analytics only, no ML required)

---
[« Back to Index](../00%20-%20Index.md)
