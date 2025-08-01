[« Back to Index](../00%20-%20Index.md)

# Partitioning

Partitioning is dividing large tables into smaller, more manageable pieces based on a column's values.

## Why Partition?
- **Performance**: Queries can skip scanning irrelevant partitions (Partition Pruning).
- **Manageability**: Easier to archive or drop old data.
- **ETL Efficiency**: Allows overriding a specific partition (e.g., re-running yesterday's data).

## Our Strategy
We partition our Bronze and Silver data lakes by **Date** (`year/month/day`). This aligns perfectly with our daily incremental batch processing.

---
[« Back to Index](../00%20-%20Index.md)
