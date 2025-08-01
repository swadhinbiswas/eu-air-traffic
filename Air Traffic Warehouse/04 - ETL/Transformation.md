[« Back to Index](../00%20-%20Index.md)

# Transformation

Transformation is where business logic is applied to raw data to make it analytical-ready.

## Tasks
- **Cleaning**: Removing nulls or replacing them with default values.
- **Standardization**: Converting all timestamps to UTC.
- **Validation**: Filtering out invalid airport codes or negative delays.
- **Enrichment**: Joining flights with weather data.
- **Modeling**: Reshaping data into Fact and Dimension tables.

## Tool
We use **Polars** for fast, in-memory transformations before loading into DuckDB.

---
[« Back to Index](../00%20-%20Index.md)
