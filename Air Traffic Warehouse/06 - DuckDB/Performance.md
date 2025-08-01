[« Back to Index](../00%20-%20Index.md)

# DuckDB Performance

DuckDB achieves high performance through several mechanisms:

1. **Columnar execution**: Processes data in chunks of columns (vectors) rather than row-by-row.
2. **Parallel execution**: Automatically utilizes multiple CPU cores for queries.
3. **Pushdown**: When querying Parquet files, DuckDB pushes filters (`WHERE` clauses) down to the file reader, skipping irrelevant data blocks.

In our platform, DuckDB ensures dashboard queries complete in milliseconds.

---
[« Back to Index](../00%20-%20Index.md)
