[« Back to Index](../00%20-%20Index.md)

# DuckDB Warehouse Design

While DuckDB can query raw files, for optimal dashboard performance, we load the curated Star Schema into a persistent `.duckdb` file.

## Structure
- We maintain `air_traffic.duckdb`.
- It contains materialized tables for the Dimensions and Facts.
- dbt connects directly to this file to generate the Gold layer (marts).
- Apache Superset connects to this file via the duckdb SQLAlchemy driver to serve dashboards.

---
[« Back to Index](../00%20-%20Index.md)
