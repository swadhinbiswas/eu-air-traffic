[« Back to Index](../00%20-%20Index.md)

# Optimization

Writing efficient SQL is critical for dashboard performance.

## Techniques
- **Select only needed columns** (especially important for columnar databases like DuckDB).
- **Filter early**: Apply `WHERE` clauses inside CTEs before joining large tables.
- **Avoid `SELECT *`**.
- **Use approximate functions**: E.g., DuckDB's `approx_count_distinct()` for large datasets where exact precision isn't strictly required.

---
[« Back to Index](../00%20-%20Index.md)
