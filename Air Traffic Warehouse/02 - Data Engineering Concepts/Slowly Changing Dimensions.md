[« Back to Index](../00%20-%20Index.md)

# Slowly Changing Dimensions (SCD)

SCDs are techniques to manage changes in dimension attributes over time.

## SCD Types
- **Type 0**: Retain original. No changes allowed.
- **Type 1**: Overwrite. Keeps only the latest state. (Simple but loses history).
- **Type 2**: Add new row. Tracks full historical changes using effective dates (`valid_from`, `valid_to`, `is_current`).
- **Type 3**: Add new column. Tracks only the previous and current state.

## Strategy in Air Traffic Platform
For simplicity in this portfolio project, we mostly rely on **Type 1** for `dim_airport` and `dim_airline`. If an airport name changes, we overwrite it. For highly mutable attributes, we treat them as facts or separate rapidly changing dimensions.

---
[« Back to Index](../00%20-%20Index.md)
