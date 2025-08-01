[« Back to Index](../00%20-%20Index.md)

# Aggregation

Aggregations summarize data. Essential for BI dashboards.

## Common Functions
- `SUM()`, `AVG()`, `COUNT()`, `MIN()`, `MAX()`

## Usage
We use aggregations extensively in our Gold layer (dbt marts) to create summary tables like `airport_daily_metrics`:
```sql
SELECT 
  date_key, departure_airport_key, 
  COUNT(*) as total_flights, 
  AVG(delay_minutes) as avg_delay
FROM fact_flights
GROUP BY 1, 2;
```

---
[« Back to Index](../00%20-%20Index.md)
