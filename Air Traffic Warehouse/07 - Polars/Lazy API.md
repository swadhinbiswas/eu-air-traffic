[« Back to Index](../00%20-%20Index.md)

# Polars Lazy API

Polars offers both Eager and Lazy execution. The Lazy API builds a query plan and optimizes it before execution.

## Benefits
- **Predicate Pushdown**: Filters are applied as early as possible.
- **Projection Pushdown**: Only required columns are read into memory.

## Example
```python
q = pl.scan_parquet("data/*.parquet").filter(pl.col("delay") > 0).select(["flight_id", "delay"])
result = q.collect()  # Execution happens here
```

---
[« Back to Index](../00%20-%20Index.md)
