[« Back to Index](../00%20-%20Index.md)

# Star Schema

A star schema is a standard data modeling technique used in data warehouses. It consists of one central fact table surrounded by multiple dimension tables, resembling a star.

## Benefits in our platform
- **Simplicity**: Easy for analysts to understand and query.
- **Performance**: Reduced number of joins compared to normalized schemas (like Snowflake).
- **Aggregation**: Fast aggregations over large datasets.

## Our Implementation
- **Fact Table**: `fact_flights`
- **Dimension Tables**: `dim_airport`, `dim_airline`, `dim_weather`, `dim_date`, `dim_fuel`

---
[« Back to Index](../00%20-%20Index.md)
