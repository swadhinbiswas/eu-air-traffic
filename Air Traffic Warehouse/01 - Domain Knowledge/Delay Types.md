[« Back to Index](../00%20-%20Index.md)

# Delay Types

## Why Classification Matters

Not all delays are equal. Classifying delay types enables:
- **Root cause analysis**: Pinpoint systemic issues
- **Accountability**: Weather delays ≠ airline operational failures
- **Mitigation**: Airlines can fix controllable delays, can't fix weather
- **Regulatory compliance**: EU261 compensation depends on delay cause

## Primary Delay Categories

### 1. Weather Delays (Code: WEATHER)
```
Cause: Thunderstorms, snow, fog, high winds, extreme temperatures
Controllable: No
Regulatory treatment: "Extraordinary circumstances" (no compensation required in EU)
```
**Detection in data:**
```sql
-- Flight delayed AND weather was adverse at departure airport
WHERE delay_minutes > 15 AND is_adverse = TRUE
```

### 2. Air Carrier Delays (Code: CARRIER)
```
Cause: Aircraft cleaning, catering, baggage loading, fueling, crew availability
Controllable: Yes (within airline's operational control)
Regulatory treatment: Compensable in EU (EU261 applies)
```
**Examples:**
- Late crew arrival from previous flight
- Aircraft maintenance not completed on time
- Baggage loading delays
- Late catering truck

### 3. Late Aircraft Arrival (Code: LATE_AIRCRAFT)
```
Cause: Incoming aircraft arrived late → cascading delay on next flight
Controllable: Partially (airline schedule padding decision)
```
**Significance:** Most common delay type (~35–40% of all delays). Tight turnarounds amplify.

### 4. National Aviation System (NAS) (Code: NAS)
```
Cause: ATC restrictions, airport congestion, airspace closures
Controllable: No (government/infrastructure issue)
```
**Examples:**
- Ground delay programs (GDPs)
- Airspace Flow Programs (AFPs)
- Airport arrival rate reduced due to volume
- ATC staffing shortages

### 5. Security Delays (Code: SECURITY)
```
Cause: Security checkpoint lines, terminal evacuation, bomb threats
Controllable: No (external)
```

### 6. Passenger Delays (Code: PASSENGER)
```
Cause: Late passengers, missing passengers, gate disputes
Controllable: Partially (airline can choose to offload and depart)
```

## FAA Delay Coding Standard

FAA assigns delay codes to every US flight:

| Category | FAA Code | Weight |
|----------|----------|--------|
| Air Carrier | 1 | 35–40% |
| Late Arriving Aircraft | 2 | 35–40% |
| NAS | 3 | 15–25% |
| Weather | 4 | 5–15% |
| Security | 5 | <1% |
| Other/Unknown | 0 | <1% |

**Note:** In the FAA system, a flight can have **multiple delay causes** assigned. The primary cause is the biggest contributor.

## EU Delay Classification (EU261/2004)

European regulation is passenger-centric. Key rules:

| Delay Duration | Distance | Compensation |
|---------------|----------|--------------|
| 3+ hours | < 1,500 km | €250 |
| 3+ hours | 1,500–3,500 km | €400 |
| 3–4 hours | > 3,500 km | €300 |
| 4+ hours | > 3,500 km | €600 |

**Exception:** "Extraordinary circumstances" (weather, ATC strikes, security) = no compensation.

## Approach for This Project

Since raw data rarely includes delay cause codes (FAA proprietary), we use **derived classification**:

```python
def classify_delay(flight, weather) -> str:
    """
    Heuristic delay classification based on available data.
    """
    if flight["status"] == "C":
        return "CANCELLED"

    if flight["delay_minutes"] <= 0:
        return "ON_TIME"  # or early

    # Weather delay: adverse weather at departure
    if weather["is_adverse"] == True:
        return "WEATHER"

    # Carrier delay: no weather, significant delay
    if flight["delay_minutes"] > 30:
        return "CARRIER"

    # Minor operational delays
    return "OPERATIONAL"
```

## Delay Distribution Analysis

In Gold layer `delay_analysis`:

```sql
CREATE OR REPLACE VIEW gold.delay_distribution AS
SELECT
    delay_category,
    COUNT(*) AS flight_count,
    ROUND(AVG(delay_minutes), 1) AS avg_delay,
    ROUND(SUM(delay_minutes) / 60.0, 1) AS total_delay_hours,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct_of_delays
FROM fact_delays
GROUP BY delay_category
ORDER BY flight_count DESC;
```

## Delay Metrics by Airport

```sql
-- Airports with worst departure delays
SELECT
    da.airport_code,
    da.airport_name,
    COUNT(*) AS departing_flights,
    ROUND(AVG(ff.delay_minutes), 1) AS avg_departure_delay,
    ROUND(PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY ff.delay_minutes), 1) AS p99_delay
FROM fact_flights ff
JOIN dim_airport da ON ff.departure_airport_key = da.airport_key
WHERE ff.delay_minutes > 0
GROUP BY da.airport_code, da.airport_name
HAVING COUNT(*) > 100
ORDER BY avg_departure_delay DESC
LIMIT 20;
```

## Delay Cascading (LATE_AIRCRAFT)

A single delay can cascade across multiple subsequent flights:

```
Flight AA100 (JFK→LAX) delayed 45 min (weather)

↓ causes

Flight AA100 (LAX→SFO) delayed 30 min (late aircraft)

↓ causes

Flight AA100 (SFO→SEA) delayed 15 min (late aircraft)
```

**Impact:** One weather event → 3 delayed flights, affecting 500+ passengers.

To detect cascades in the Gold layer:
```sql
SELECT
    aircraft_registration,
    scheduled_departure,
    actual_departure,
    delay_minutes,
    LAG(delay_minutes) OVER (
        PARTITION BY aircraft_registration
        ORDER BY scheduled_departure
    ) AS previous_flight_delay
FROM fact_flights;
```

## Delay Cost Estimation

For business impact analysis, approximate cost:

| Delay Duration | Estimated Cost per Flight |
|---------------|--------------------------|
| < 15 min | $0 (within buffer) |
| 15–60 min | $500–$2,000 (crew overtime, fuel) |
| 1–3 hours | $5,000–$15,000 (passenger rebooking, crew duty) |
| > 3 hours | $25,000–$150,000 (EU261 compensation, hotel, full disruption) |

```sql
-- Estimate total delay cost
SELECT
    CASE
        WHEN delay_minutes <= 15 THEN '$0'
        WHEN delay_minutes <= 60 THEN '$500–$2,000'
        WHEN delay_minutes <= 180 THEN '$5,000–$15,000'
        ELSE '$25,000+'
    END AS cost_bracket,
    COUNT(*) AS flights,
    SUM(delay_minutes) AS total_delay_minutes
FROM fact_flights
WHERE delay_minutes > 0
GROUP BY
    CASE
        WHEN delay_minutes <= 15 THEN '$0'
        WHEN delay_minutes <= 60 THEN '$500–$2,000'
        WHEN delay_minutes <= 180 THEN '$5,000–$15,000'
        ELSE '$25,000+'
    END;
```

## Data Quality Validation: Delays

```python
# Polars validation rules for flight delays
validation_rules = [
    (pl.col("delay_minutes") >= 0, "Negative delay"),
    (pl.col("delay_minutes") <= 1440, "Delay > 24 hours (unrealistic)"),
    (
        pl.col("actual_departure") > pl.col("scheduled_departure"),
        "Early departure with positive delay — inconsistent",
    ),
]
```

## How Delay Data Appears in Warehouse

`fact_flights` stores the raw delay minutes.
`fact_delays` stores delay category breakdowns (one flight can have multiple delay reasons).

```sql
-- fact_delays structure
SELECT
    flight_key,
    delay_reason AS category,  -- 'WEATHER', 'CARRIER', 'NAS', 'LATE_AIRCRAFT'
    delay_minutes AS category_minutes
FROM fact_delays;
```

This allows analysis queries like: "How many minutes of delay were caused by weather in 2024?"

```sql
SELECT SUM(category_minutes) / 60.0 AS total_weather_delay_hours
FROM fact_delays
WHERE delay_reason = 'WEATHER';
```

---
[« Back to Index](../00%20-%20Index.md)
