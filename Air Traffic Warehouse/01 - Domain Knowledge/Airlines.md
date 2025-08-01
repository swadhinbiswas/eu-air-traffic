[« Back to Index](../00%20-%20Index.md)

# Airlines

## Airline Business Model Context

Understanding how airlines operate helps design meaningful analytics:

### Revenue Sources
| Source | % of Revenue (typical) |
|--------|------------------------|
| Passenger tickets | 70–80% |
| Cargo | 10–15% |
| Ancillary (bags, seats, meals) | 5–10% |
| Loyalty program partnerships | 3–5% |

### Cost Structure
| Cost | % of Total |
|------|------------|
| Fuel | 20–30% |
| Labor (crew, ground staff) | 25–35% |
| Aircraft ownership/lease | 15–20% |
| Maintenance | 10–15% |
| Airport fees + ATC charges | 5–10% |
| Others (marketing, distribution) | 5–10% |

### Why Delays Cost Money

For a typical airline, each **minute of delay costs approximately $75–$150** (fuel, crew overtime, passenger compensation, missed connections). This is why delay analysis is economically critical.

## Major Airlines Tracked in This Project

### US Legacy Carriers

| IATA | ICAO | Airline | Hub(s) |
|------|------|---------|--------|
| DL | DAL | Delta Air Lines | ATL, DTW, MSP, SEA, JFK |
| AA | AAL | American Airlines | DFW, CLT, MIA, ORD, PHL |
| UA | UAL | United Airlines | EWR, IAH, SFO, DEN, ORD |

### US Low-Cost Carriers

| IATA | ICAO | Airline | Focus |
|------|------|---------|-------|
| WN | SWA | Southwest Airlines | DAL, MDW, LAS, PHX |
| NK | NKS | Spirit Airlines | FLL, LAS, DTW |
| F9 | FFT | Frontier Airlines | DEN, MCO, LAS |
| B6 | JBU | JetBlue Airways | JFK, BOS, FLL |

### Major European Carriers

| IATA | ICAO | Airline | Hub(s) |
|------|------|---------|--------|
| LH | DLH | Lufthansa | FRA, MUC |
| BA | BAW | British Airways | LHR, LGW |
| AF | AFR | Air France | CDG |
| KL | KLM | KLM Royal Dutch | AMS |
| FR | RYR | Ryanair (LCC) | DUB, STN |
| U2 | EZY | easyJet (LCC) | LGW, BRS, MXP |

### Major Asian/Middle Eastern Carriers

| IATA | ICAO | Airline | Hub |
|------|------|---------|-----|
| EK | UAE | Emirates | DXB |
| QR | QTR | Qatar Airways | DOH |
| SQ | SIA | Singapore Airlines | SIN |
| CX | CPA | Cathay Pacific | HKG |
| JL | JAL | Japan Airlines | HND, NRT |
| NH | ANA | All Nippon Airways | HND, NRT |

## Airline Data Model (`dim_airline`)

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `airline_key` | INTEGER (SK) | Surrogate key | 42 |
| `airline_code` | VARCHAR(2) | IATA 2-letter code | DL |
| `airline_code_icao` | VARCHAR(3) | ICAO 3-letter code | DAL |
| `airline_name` | VARCHAR(100) | Full airline name | Delta Air Lines |
| `country` | VARCHAR(50) | Country of registration | United States |
| `carrier_type` | VARCHAR(20) | FSC/LCC/Regional/Cargo | FSC |
| `alliance` | VARCHAR(20) | Alliance membership | SkyTeam |
| `fleet_size` | INTEGER | Approximate aircraft count | 950 |
| `is_active` | BOOLEAN | Currently operating | TRUE |

## Airline Performance KPIs

```sql
-- Airline on-time performance ranking
SELECT
    dim_airline.airline_code,
    dim_airline.airline_name,
    COUNT(*) AS total_flights,
    ROUND(AVG(fact_flights.delay_minutes), 1) AS avg_delay_min,
    ROUND(SUM(CASE WHEN delay_minutes <= 15 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS on_time_pct,
    ROUND(SUM(CASE WHEN status = 'C' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS cancellation_pct
FROM fact_flights
JOIN dim_airline ON fact_flights.airline_key = dim_airline.airline_key
GROUP BY dim_airline.airline_code, dim_airline.airline_name
ORDER BY on_time_pct DESC;
```

## Delay Patterns by Airline Type

| Carrier Type | Typical Delay Profile |
|-------------|----------------------|
| FSC (legacy) | Hub congestion, connecting passenger delays |
| LCC | Faster turnarounds (25–35 min), cascading delays |
| Regional | Weather-sensitive, dependent on mainline partner |
| Cargo | Night operations, fewer weather delays |

## Airline Alliances

Alliances affect routing — useful for network analysis:

| Alliance | Members |
|----------|---------|
| **Star Alliance** | UA, LH, SQ, NH, TK, SK, LX |
| **SkyTeam** | DL, AF, KL, KE, AM, MU |
| **oneworld** | AA, BA, CX, QF, JL, QR |

Alliance membership influences code-sharing and hub choice.

## Airline SCD Strategy

Airline details change infrequently:
- **Type 1 SCD**: Overwrite (name changes, alliance shifts)
- This project uses **Type 1 SCD** for airlines (simplicity, low churn)

## Airline Data Sources

| Source | Coverage | Format |
|--------|----------|--------|
| OpenFlights | ~6,000 airlines (historical + current) | .dat CSV |
| OurAirports | Airport-airline linkage | CSV |
| IATA Directory | Official codes | Website |
| Wikipedia | Fleet size, hubs | Scraping (avoid) |

We use **OpenFlights airlines.dat** as the primary reference:

```text
airline_id,airline_name,alias,iata,icao,callsign,country,active
24,Delta Air Lines Inc.,Delta,DL,DAL,DELTA,United States,Y
```

```python
# Polars: Load and filter active airlines
import polars as pl

schema = {
    "id": pl.Int32, "name": pl.Utf8, "alias": pl.Utf8,
    "iata": pl.Utf8, "icao": pl.Utf8, "callsign": pl.Utf8,
    "country": pl.Utf8, "active": pl.Utf8
}

airlines = (
    pl.read_csv("airlines.dat", has_header=False, new_columns=list(schema.keys()))
    .filter(pl.col("active") == "Y")
    .filter(pl.col("iata") != "")       # Only airlines with IATA code
    .filter(pl.col("icao") != "")       # Only airlines with ICAO code
)
```

---
[« Back to Index](../00%20-%20Index.md)
