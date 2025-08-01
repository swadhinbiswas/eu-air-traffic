[« Back to Index](../00%20-%20Index.md)

# Ranking

Ranking functions assign a rank to each row within a partition of a result set.

## Functions
- `ROW_NUMBER()`: Unique number for each row.
- `RANK()`: Same rank for ties, leaves gaps.
- `DENSE_RANK()`: Same rank for ties, no gaps.

## Example
Ranking airports by worst average delays:
```sql
SELECT airport_name, avg_delay,
       RANK() OVER (ORDER BY avg_delay DESC) as delay_rank
FROM airport_metrics;
```

---
[« Back to Index](../00%20-%20Index.md)
