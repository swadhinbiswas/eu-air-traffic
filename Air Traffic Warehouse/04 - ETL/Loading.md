[« Back to Index](../00%20-%20Index.md)

# Loading

Loading is the process of writing the transformed data into the target destination.

## Types of Loads
- **Full Load**: Truncating the target table and reloading everything. (Used for small dimension tables).
- **Incremental Load**: Appending or upserting only new/changed records.

## Our Implementation
We load the transformed DataFrames into DuckDB. For Fact tables, we perform incremental appends. For Dimension tables, we perform upserts (using DuckDB's `INSERT ... ON CONFLICT`).

---
[« Back to Index](../00%20-%20Index.md)
