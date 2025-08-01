[« Back to Index](../00%20-%20Index.md)

# Window Functions

Window functions perform calculations across a set of table rows that are somehow related to the current row, without grouping them into a single output row.

## Examples in our Platform
- Calculating the **moving average** of flight delays per airport.
- Finding the **cumulative sum** of cancelled flights over a month.
- Using `LAG()` to find the delay difference between a flight and the previous flight on the same route.

```sql
SELECT flight_id, delay_minutes,
       AVG(delay_minutes) OVER (PARTITION BY airline_code ORDER BY departure_time) as avg_airline_delay
FROM fact_flights;
```

---
[« Back to Index](../00%20-%20Index.md)
