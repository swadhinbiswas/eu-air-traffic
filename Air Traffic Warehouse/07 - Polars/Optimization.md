[« Back to Index](../00%20-%20Index.md)

# Polars Optimization

To maximize Polars performance:

1. **Always use Lazy API** (`scan_parquet` instead of `read_parquet`) when reading large datasets.
2. **Avoid `apply`**: Custom Python functions break the Rust optimizations. Stick to native Polars expressions.
3. **Use Categorical types** for string columns with low cardinality (like `airline_code` or `weather_condition`) to save memory and speed up joins.

---
[« Back to Index](../00%20-%20Index.md)
