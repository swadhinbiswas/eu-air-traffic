[« Back to Index](../00%20-%20Index.md)

# ADR: Why Polars?

**Context**: We need a library to perform data transformations (ETL).

**Options Considered**: Pandas, PySpark, Polars.

**Decision**: Polars.

**Rationale**:
- PySpark is overkill (too much overhead) for single-node processing.
- Pandas is slow and memory-inefficient for large datasets.
- Polars offers blazing fast multi-threaded execution and a clean, expressive API.

---
[« Back to Index](../00%20-%20Index.md)
