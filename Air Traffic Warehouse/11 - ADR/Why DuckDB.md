[« Back to Index](../00%20-%20Index.md)

# ADR: Why DuckDB?

**Context**: We need an analytical SQL engine to serve as our Data Warehouse.

**Options Considered**: PostgreSQL, Snowflake, BigQuery, DuckDB.

**Decision**: DuckDB.

**Rationale**:
- **Cost**: 100% free. No cloud compute costs.
- **Simplicity**: No server setup, embedded database.
- **Performance**: Columnar and vectorized, easily handling millions of rows in milliseconds on a laptop.
- **Portability**: Perfect for a reproducible portfolio project.

---
[« Back to Index](../00%20-%20Index.md)
