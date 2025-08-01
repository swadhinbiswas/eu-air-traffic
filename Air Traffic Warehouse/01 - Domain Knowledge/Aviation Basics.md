[« Back to Index](../00%20-%20Index.md)

# Aviation Basics

## The Aviation Ecosystem

### Key Entities

| Entity | Description | Examples |
|--------|-------------|----------|
| **Airline** | Company operating flights | Delta (DL), United (UA), Lufthansa (LH) |
| **Airport** | Facility for takeoff/landing | JFK, LAX, LHR, DXB, SIN |
| **Flight** | A single scheduled journey | AA100: JFK → LAX |
| **Route** | City-pair service | New York → London |
| **Aircraft** | Physical plane model | Boeing 737, Airbus A320 |

### Flight Lifecycle

```text
Boarding → Pushback → Taxi Out → Takeoff → Cruise → Landing → Taxi In → Gate Arrival
```

Key time points:
- **STD** (Scheduled Time of Departure): Planned pushback time
- **ATD** (Actual Time of Departure): Real pushback time
- **STA** (Scheduled Time of Arrival): Planned gate arrival time
- **ATA** (Actual Time of Arrival): Real gate arrival time

### Flight Status Codes

| Code | Meaning |
|------|---------|
| S | Scheduled (on-time, not yet operated) |
| A | Active (in-flight) |
| L | Landed (arrived safely) |
| C | Cancelled |
| D | Diverted (landed at alternate airport) |
| R | Redirected |
| N | No info available |

## ICAO vs IATA Codes

### IATA Codes (2-letter airline, 3-letter airport)
- Used by the public (tickets, booking systems)
- Airline: DL = Delta, UA = United
- Airport: JFK, LAX, LHR

### ICAO Codes (3-letter airline, 4-letter airport)
- Used by ATC, pilots, industry
- Airline: DAL = Delta, UAL = United
- Airport: KJFK, KLAX, EGLL

**Important:** In this project, we use **IATA codes** (more common in public datasets).

## Flight Numbers

Format: `[Airline Code][Number]`
- AA100: American Airlines flight 100
- DL1234: Delta flight 1234

Flight numbers can repeat daily — they are **not globally unique**. A composite key of `(airline_code, flight_number, date)` uniquely identifies a flight.

## Key Aviation Metrics

| Metric | Formula | Unit |
|--------|---------|------|
| **Delay Minutes** | ATD - STD (or ATA - STA) | minutes |
| **Flight Duration** | ATA - ATD (actual arrival - actual departure) | minutes |
| **Distance** | Great-circle distance between airport coordinates | km |
| **Cancellation Rate** | Cancelled flights / Total flights | % |
| **On-Time Performance** | Flights with delay ≤ 15min / Total flights | % |

## Delay Definitions

| Delay Category | Threshold |
|---------------|-----------|
| On-time | ≤ 15 minutes |
| Minor delay | 15–60 minutes |
| Significant delay | 60–120 minutes |
| Severe delay | > 120 minutes |

Note: Industry standard considers 15 minutes the threshold for "on-time" (FAA/EU standard).

## Types of Airlines

| Type | Characteristics | Examples |
|------|----------------|-----------|
| **Full-Service Carrier (FSC)** | Full amenities, hub-and-spoke | Delta, Lufthansa |
| **Low-Cost Carrier (LCC)** | No frills, point-to-point | Ryanair, Southwest |
| **Regional** | Small planes, short routes | SkyWest, Horizon |
| **Charter** | Non-scheduled, seasonal | TUI, Condor |
| **Cargo** | Freight only | FedEx, UPS, Cargolux |

## Hub-and-Spoke vs Point-to-Point

### Hub-and-Spoke
```
Origin → HUB → HUB → Destination
```
Legacy carriers route through central hubs.

### Point-to-Point
```
Origin → Destination (direct)
```
LCCs fly direct routes, avoiding hubs.

## Aviation Data Sources

| Source | Data | Access |
|--------|------|--------|
| FlightAware | Real-time flights | API (paid) |
| OpenSky Network | ADS-B flight tracks | Free API |
| FlightRadar24 | Live flight tracking | API (paid) |
| FAA/ASPM | US flight data | Public datasets |
| Eurocontrol | European ATC data | Aggregated reports |
| OurAirports | Airport database | Open dataset |
| OpenFlights | Routes + airports | Community dataset |

## Time Zone Considerations

- All timestamps should be normalized to **UTC** for storage
- Local airport time zones are needed for business analysis (e.g., "morning flights at JFK")
- `dim_airport` stores `timezone` for this purpose
- `dim_date` allows timezone-aware aggregations

```python
# Example: Convert airport local time to UTC
from datetime import datetime
import pytz

local_time = datetime(2024, 6, 15, 8, 0)  # 8 AM local
tz = pytz.timezone("America/New_York")
utc_time = tz.localize(local_time).astimezone(pytz.UTC)
```

## Seasonal Aviation Patterns

| Season | Characteristics |
|--------|----------------|
| Summer (Jun–Aug) | Peak travel, more delays, thunderstorms |
| Winter (Dec–Feb) | De-icing delays, snow closures |
| Spring Break (Mar) | High leisure travel |
| Thanksgiving (Nov) | Highest US domestic travel |
| Christmas (Dec) | Peak international travel |

These patterns are important for seasonal trend analysis in the Gold layer.

---
[« Back to Index](../00%20-%20Index.md)
