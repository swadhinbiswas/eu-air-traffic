[« Back to Index](../00%20-%20Index.md)

# Dimension Tables

Dimension tables provide context to the facts. They contain descriptive attributes that are used to filter, group, and label data in reports.

## Characteristics
- Usually smaller than fact tables.
- Denormalized (flattened) for query performance.
- Contain a primary key (surrogate key) that links to the fact table.

## Our Dimensions
- `dim_airport`: Airport codes, names, locations.
- `dim_airline`: Airline names and codes.
- `dim_weather`: Weather conditions and metrics.
- `dim_date`: Date attributes (year, month, is_weekend, etc.).

---
[« Back to Index](../00%20-%20Index.md)
