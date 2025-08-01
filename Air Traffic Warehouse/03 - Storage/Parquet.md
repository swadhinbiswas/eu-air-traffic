[« Back to Index](../00%20-%20Index.md)

# Parquet

Apache Parquet is a columnar storage file format optimized for analytics.

## Advantages
- **Columnar**: Stores data by column, allowing analytical queries (which often select only a few columns) to skip reading unnecessary data.
- **Compression**: Highly compressible because data in the same column is homogeneous.
- **Schema Evolution**: Supports schema evolution.

## Usage in Platform
Parquet is the primary storage format for our Bronze, Silver, and Gold data lake layers. DuckDB natively queries Parquet files with exceptional performance.

---
[« Back to Index](../00%20-%20Index.md)
