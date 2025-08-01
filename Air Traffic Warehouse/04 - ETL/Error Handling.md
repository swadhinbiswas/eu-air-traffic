[« Back to Index](../00%20-%20Index.md)

# Error Handling

Robust pipelines must anticipate and gracefully handle failures.

## Strategies
- **API Failures**: Retry with exponential backoff. Alert if persistent.
- **Validation Failures**: Log bad records, do not fail the entire batch unless error rate > threshold (e.g., 5%).
- **Database Locks**: DuckDB is single-writer. Ensure pipeline orchestrator prevents concurrent write runs.
- **Notifications**: Send alerts to a webhook (Slack/Discord) if a pipeline step fails.

---
[« Back to Index](../00%20-%20Index.md)
