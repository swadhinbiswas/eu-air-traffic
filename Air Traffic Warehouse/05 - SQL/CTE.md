[« Back to Index](../00%20-%20Index.md)

# CTE (Common Table Expressions)

CTEs make complex SQL queries more readable by breaking them into simpler, logical blocks using the `WITH` clause.

## Example
```sql
WITH delayed_flights AS (
    SELECT * FROM fact_flights WHERE delay_minutes > 15
)
SELECT airline_name, COUNT(*) as delayed_count
FROM delayed_flights
JOIN dim_airline USING (airline_key)
GROUP BY 1;
```
All our dbt models heavily utilize CTEs for clean, modular SQL code.

---
[« Back to Index](../00%20-%20Index.md)
