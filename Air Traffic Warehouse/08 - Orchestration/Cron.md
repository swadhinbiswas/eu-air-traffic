[« Back to Index](../00%20-%20Index.md)

# Cron Scheduling

Cron is a standard time-based job scheduler. GitHub Actions uses cron syntax for scheduling workflows.

## Our Schedule
`0 */6 * * *` - Runs at minute 0 past every 6th hour.

This ensures our dashboard is updated 4 times a day with the latest flight data.

---
[« Back to Index](../00%20-%20Index.md)
