[« Back to Index](../00%20-%20Index.md)

# Scheduling Strategy

Our batch scheduling strategy must handle dependencies:

1. Ingest Flights & Weather (Parallel)
2. Transform Bronze to Silver
3. Load Silver to Warehouse
4. Run dbt (Silver to Gold)

Since everything runs within a single Python orchestrator script executed by GitHub Actions, the script internally manages this sequential execution flow.

---
[« Back to Index](../00%20-%20Index.md)
