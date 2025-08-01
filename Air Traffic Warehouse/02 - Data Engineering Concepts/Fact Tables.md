[« Back to Index](../00%20-%20Index.md)

# Fact Tables

Fact tables contain the quantitative data or metrics of a business process. They are typically very large and contain foreign keys to dimension tables.

## Types of Fact Tables
1. **Transaction Fact Tables**: One row per event (e.g., a flight departing).
2. **Periodic Snapshot Fact Tables**: One row per period (e.g., daily total flights per airport).
3. **Accumulating Snapshot Fact Tables**: One row for the entire lifecycle of an event.

## Our Fact Tables
- `fact_flights`: Transaction grain (one row per flight).
- `fact_delays`: Transaction grain containing specific delay reasons.

---
[« Back to Index](../00%20-%20Index.md)
