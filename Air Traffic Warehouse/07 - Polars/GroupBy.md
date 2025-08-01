[« Back to Index](../00%20-%20Index.md)

# GroupBy in Polars

Grouping data is a core transformation operation.

## Syntax
```python
df.group_by("airline_code").agg(
    [
        pl.col("delay_minutes").mean().alias("avg_delay"),
        pl.col("flight_id").count().alias("total_flights"),
    ]
)
```
Polars executes these groupings in parallel, making it vastly superior to Pandas for large datasets.

---
[« Back to Index](../00%20-%20Index.md)
