[« Back to Index](../00%20-%20Index.md)

# Querying Parquet with DuckDB

DuckDB can query Parquet files directly without loading them into the database file first.

## Example
```sql
SELECT airport, count(*)
FROM read_parquet('warehouse/silver/flights/*.parquet')
GROUP BY airport;
```
This allows us to treat our file system (Data Lake) as if it were a database, enabling rapid prototyping and data validation.

---
[« Back to Index](../00%20-%20Index.md)
