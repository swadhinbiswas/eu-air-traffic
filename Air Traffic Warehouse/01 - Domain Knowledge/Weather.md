[« Back to Index](../00%20-%20Index.md)

# Weather

## Weather and Aviation

Weather is the **#1 cause of flight delays globally**, responsible for ~30–40% of all delay minutes (FAA data). Understanding weather patterns is critical for delay analysis.

## Weather Variables Tracked

| Variable | Unit | Range | Impact on Aviation |
|----------|------|-------|-------------------|
| **Temperature** | °C | -80 to 60 | Extreme heat reduces lift; extreme cold needs de-icing |
| **Humidity** | % | 0 to 100 | Fog formation; engine performance |
| **Wind Speed** | km/h | 0 to 300 | Crosswind limits; headwind/tailwind affects fuel |
| **Visibility** | km | 0 to 100 | Instrument approaches required when < 1.6 km |
| **Precipitation** | mm/h | — | Thunderstorms, snow, ice |
| **Pressure** | hPa | — | Takeoff/landing performance |
| **Weather Condition** | category | — | Clear, Rain, Snow, Fog, Thunderstorm |

## Weather Condition Categories

| Category | ICAO Code | Aviation Impact |
|----------|-----------|-----------------|
| Clear/Sunny | CAVOK | Normal operations |
| Rain | RA | Reduced visibility, wet runway |
| Heavy Rain | +RA | Possible ground stops |
| Snow | SN | Runway closures, de-icing delays |
| Blizzard | BLSN | Airport closure |
| Fog | FG | Low visibility, CAT III approaches |
| Thunderstorm | TS | Lightning (ramp closures), turbulence |
| Wind shear | WS | Go-arounds, diversions |
| Volcanic ash | VA | Complete airspace closure |

## Weather Impact Scaling

| Weather Severity | Typical Delay Impact |
|-----------------|---------------------|
| Light rain | +5–15 min |
| Heavy rain / moderate snow | +30–60 min |
| Thunderstorm at hub | +2–4 hours (ground stop) |
| Fog at hub | +1–3 hours (reduced arrival rate) |
| Winter storm | Airport closure (hours–days) |

## Weather Data Model (`dim_weather`)

| Field | Type | Description |
|-------|------|-------------|
| `weather_key` | INTEGER (SK) | Surrogate key |
| `station_code` | VARCHAR(4) | Weather station ICAO code |
| `airport_code` | VARCHAR(3) | Associated airport |
| `observation_time` | TIMESTAMP | UTC timestamp |
| `temperature_c` | DECIMAL(5,1) | Temperature in Celsius |
| `dew_point_c` | DECIMAL(5,1) | Dew point |
| `humidity_pct` | DECIMAL(5,1) | Relative humidity % |
| `wind_speed_kmh` | DECIMAL(5,1) | Wind speed |
| `wind_direction` | INTEGER | Wind direction in degrees |
| `visibility_km` | DECIMAL(5,1) | Visibility |
| `pressure_hpa` | DECIMAL(6,1) | Atmospheric pressure |
| `precipitation_mm` | DECIMAL(5,1) | Precipitation |
| `condition` | VARCHAR(30) | Weather condition category |
| `is_adverse` | BOOLEAN | Computed: conditions likely to cause delays |

## Adversity Classification Logic

```python
def classify_adverse_weather(row):
    """Return True if weather is likely to cause delays."""
    return (
        row["visibility_km"] < 3.0
        or row["wind_speed_kmh"] > 50
        or row["precipitation_mm"] > 5
        or row["condition"] in ["Thunderstorm", "Snow", "Fog", "Blizzard"]
    )
```

This `is_adverse` flag is a derived column in Silver/Gold layers that makes weather-delay correlation queries simple:

```sql
SELECT
    is_adverse,
    AVG(delay_minutes) AS avg_delay,
    COUNT(*) AS flight_count
FROM fact_flights
JOIN dim_weather ON fact_flights.weather_key = dim_weather.weather_key
GROUP BY is_adverse;
```

## Weather Data Sources

| Source | Data | Access |
|--------|------|--------|
| NOAA/NWS | US airports | Free API |
| OpenWeatherMap | Global stations | Free tier (60 calls/min) |
| WeatherAPI.com | Global | Free tier (1M calls/month) |
| Visual Crossing | Historical | Free tier (1,000 records/day) |
| Open-Meteo | Global, open-source | Free API, no key required ⭐ |

**Recommended for this project:** [Open-Meteo](https://open-meteo.com/) — free, no API key, global coverage.

```python
# Open-Meteo API example (no API key required)
import requests

url = "https://api.open-meteo.com/v1/forecast"
params = {
    "latitude": 40.6413,  # JFK
    "longitude": -73.7781,
    "hourly": ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "visibility"],
    "timezone": "UTC",
}
response = requests.get(url, params=params)
weather_data = response.json()
```

## Temporal Joining Strategy

Weather data is typically **hourly**. Flights have specific departure/arrival timestamps. Strategy:

1. Store weather at hourly granularity in Silver
2. When building `fact_flights`, join the **nearest hourly weather observation** within ±1 hour of scheduled departure

```python
# Polars temporal join: nearest weather for each flight
flights_with_weather = flights.sort("scheduled_departure").join_asof(
    weather.sort("observation_time"),
    left_on="scheduled_departure",
    right_on="observation_time",
    tolerance="1h",  # within 1 hour
    strategy="nearest",
)
```

## Seasonal Weather Patterns

| Season | Primary Weather Delays |
|--------|----------------------|
| Summer (Jun–Aug) | Thunderstorms at hubs (ATL, ORD, DFW) |
| Winter (Dec–Feb) | Snow/Ice (ORD, DEN, EWR, BOS), Fog (LHR, SFO) |
| Spring (Mar–May) | Wind, turbulence |
| Fall (Sep–Nov) | Hurricane season (Sept–Nov, Gulf Coast) |

These patterns feed into seasonal trend Gold tables.

## Weather Delay Quantification

```sql
-- DuckDB: Measure delay impact per weather condition
SELECT
    w.condition,
    COUNT(*) AS flights,
    ROUND(AVG(f.delay_minutes), 1) AS avg_delay,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY f.delay_minutes), 1) AS p95_delay,
    ROUND(SUM(CASE WHEN f.status = 'C' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS cancellation_rate
FROM fact_flights f
JOIN dim_weather w ON f.weather_key = w.weather_key
GROUP BY w.condition
ORDER BY avg_delay DESC;
```

## Data Quality Notes for Weather

- Missing observations: forward-fill from nearest station
- Extreme values: validate `-80 <= temperature <= 60`
- Station ↔ Airport mapping: manual lookup table
- Time zones: all weather timestamps must be UTC

```python
# Weather validation rules
def validate_weather_row(row) -> bool:
    return (
        -80 <= row["temperature_c"] <= 60
        and 0 <= row["humidity_pct"] <= 100
        and 0 <= row["wind_speed_kmh"] <= 300
        and 0 <= row["visibility_km"] <= 100
        and 800 <= row["pressure_hpa"] <= 1100
    )
```

---
[« Back to Index](../00%20-%20Index.md)
