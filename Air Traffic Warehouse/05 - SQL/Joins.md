[« Back to Index](../00%20-%20Index.md)

# Joins

Joins combine columns from one or more tables based on a related column between them.

## Types
- **INNER JOIN**: Returns records with matching values in both tables.
- **LEFT JOIN**: Returns all records from the left table, and matched records from the right. (Crucial for Fact tables joining Dimensions to avoid losing facts if a dimension is missing).

## Best Practices
In our star schema, we primarily `LEFT JOIN` the fact table to its dimensions on their respective surrogate keys.

---
[« Back to Index](../00%20-%20Index.md)
