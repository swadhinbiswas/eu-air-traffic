[« Back to Index](../00%20-%20Index.md)

# Incremental Loading

Processing only the data that has changed since the last run, rather than reprocessing everything.

## Mechanism
1. **Watermarking**: We store a `last_run.json` containing the timestamp of the last successful extraction.
2. **Filtering**: The extraction phase only requests data `updated_at > last_run_timestamp`.
3. **Upserting**: During the load phase, we handle duplicates using primary keys to ensure idempotency.

This ensures the pipeline is fast and cost-effective.

---
[« Back to Index](../00%20-%20Index.md)
