[« Back to Index](../00%20-%20Index.md)

# Data Validation

Ensuring data quality before it enters the warehouse.

## Rules
1. `delay_minutes >= 0`
2. `len(airport_code) == 3` (IATA format)
3. `arrival_time > departure_time`
4. `temperature` between -80C and 60C.
5. Uniqueness on `flight_id`.

## Implementation
Validation runs during the Bronze-to-Silver transformation step. Rows failing validation are either dropped and logged to an error table (dead letter queue), or cleaned if safely possible.

---
[« Back to Index](../00%20-%20Index.md)
