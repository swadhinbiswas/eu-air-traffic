---
language:
  - en
license: mit
tags:
  - aviation
  - ads-b
  - flight-tracking
  - meteorology
  - meteorology-aviation
  - open-data
  - parquet
  - time-series
task_categories:
  - tabular-classification
  - tabular-regression
  - time-series-forecasting
pretty_name: EU Air Traffic Lake
configs:
- config_name: silver
  data_files:
  - split: positions
    path: "silver/positions/data.parquet"
  - split: flights
    path: "silver/flights/data.parquet"
  - split: weather
    path: "silver/weather/data.parquet"
  - split: weather_forecast
    path: "silver/weather_forecast/data.parquet"
  - split: weather_taf
    path: "silver/weather_taf/data.parquet"
  - split: fuel
    path: "silver/fuel/data.parquet"
  - split: routes
    path: "silver/routes/data.parquet"
  - split: airports
    path: "silver/airports/airports.parquet"
  - split: holidays
    path: "silver/holidays/data.parquet"
  - split: aircraft
    path: "silver/aircraft/data.parquet"
  - split: notams
    path: "silver/notams/data.parquet"
  - split: emissions
    path: "silver/emissions/data.parquet"
- config_name: bronze
  data_files:
  - split: parquet
    path: "bronze/parquet/*/*.parquet"
  - split: raw
    path: "bronze/raw/*/*/*.jsonl"
---

# EU Air Traffic Lake

Live and scheduled European air traffic as versioned Parquet: raw Bronze intake windows from the collector, plus curated Silver snapshots refreshed by a scheduled pipeline.

## Updates

- Bronze windows land continuously (positions every ~5 min, weather every 5 min, departures twice an hour, arrivals backfilled nightly).
- Each of the Silver snapshots below is replaced every 15 minutes by the lake pipeline.
- The Eurostat airport-traffic benchmark and dbt Gold marts are not stored here; see [eu-air-traffic](https://github.com/swadhinbiswas/eu-air-traffic) for the warehouse.

## Contents

### Silver snapshots (`silver/<source>/data.parquet`)

- `silver/positions/data.parquet` — Live aircraft position snapshots (deduped by airframe).
  Key columns: `icao24`, `callsign`, `latitude`, `longitude`, `altitude`, `velocity`.
- `silver/flights/data.parquet` — Completed movements from OpenSky plus AirLabs schedules.
  Key columns: `flight_id`, `callsign`, `departure_icao`, `arrival_icao`, `status`, `delay_minutes`.
- `silver/weather/data.parquet` — METAR observations with flight categories.
  Key columns: `station_icao`, `timestamp`, `temperature_c`, `wind_speed_kt`, `flight_category`.
- `silver/weather_forecast/data.parquet` — Open-Meteo hourly forecasts.
  Key columns: `station_icao`, `timestamp`, `temperature_c`, `condition`.
- `silver/weather_taf/data.parquet` — Raw terminal aerodrome forecasts.
  Key columns: `station_icao`, `issue_time`, `valid_from`, `valid_to`, `raw_taf`.
- `silver/fuel/data.parquet` — Daily jet-fuel prices by region.
  Key columns: `date`, `region`, `price_per_litre`, `currency`.
- `silver/routes/data.parquet` — EU route network, ICAO-mapped with great-circle distance.
  Key columns: `airline`, `origin`, `destination`, `distance_km`, `stops`, `equipment`.
- `silver/airports/airports.parquet` — Enriched EU airport reference.
  Key columns: `ident`, `name`, `type`, `latitude_deg`, `longitude_deg`, `iata_code`.
- `silver/holidays/data.parquet` — Public-holiday calendar by country.
  Key columns: `country`, `date`, `name`.
- `silver/aircraft/data.parquet` — Aircraft type reference (ICAO 8643 + specs).
  Key columns: `type_icao`, `manufacturer`, `family`, `capacity`, `range_km`.
- `silver/notams/data.parquet` — Notices to air missions.
  Key columns: `notam_id`, `icao_location`, `notam_type`, `valid_from`, `valid_to`.
- `silver/emissions/data.parquet` — Per-type hourly fuel and CO2 rates.
  Key columns: `aircraft_type`, `fuel_burn_kg_per_hour`, `co2_kg_per_hour`.

### Bronze intake windows

- `bronze/parquet/<source>/<source>_YYYY-MM-DDTHHMMSSffffffZ.parquet` — immutable windows exactly as drained from Kafka.
- `bronze/raw/<source>/<date>/*.jsonl` — the same records before Parquet conversion.
- Windows accumulate: download a prefix such as `bronze/parquet/flights/` for history.

## Field reference

<details><summary><code>silver/positions/data.parquet</code></summary>

`icao24`, `callsign`, `registration`, `aircraft_type`, `latitude`, `longitude`, `altitude`, `altitude_geom`, `velocity`, `heading`, `vertical_rate`, `mach`, `ias`, `tas`, `oat`, `wind_dir`, `wind_speed`, `squawk`, `emergency`, `category`, `on_ground`, `source`, `collected_at`, `fuel_burn_kg_per_hour`, `co2_kg_per_hour`, `aircraft_class`, `emitter_class`, `is_cargo`, `is_military`, `operator_name`, `operator_country`, `operator_category`, `type_name`, `manufacturer`, `airframe`, `wake_category`

Note: Type-specific columns may be null (e.g. squawk only for transponders that send it).

</details>

<details><summary><code>silver/flights/data.parquet</code></summary>

`flight_id`, `callsign`, `airline_icao`, `airline_name`, `departure_icao`, `departure_iata`, `arrival_icao`, `arrival_iata`, `scheduled_departure`, `scheduled_arrival`, `actual_departure`, `actual_arrival`, `status`, `delay_minutes`, `cancelled`, `source`, `collected_at`, `ingestion_date`

Note: delay_minutes/actual_* are null when the source has no schedule (OpenSky movements).

</details>

<details><summary><code>silver/weather/data.parquet</code></summary>

`station_icao`, `timestamp`, `temperature_c`, `humidity_pct`, `wind_speed_ms`, `visibility_m`, `condition`, `pressure_hpa`, `source`, `ingestion_date`, `name`, `latitude`, `longitude`, `dewpoint_c`, `wind_dir_deg`, `wind_speed_kt`, `gust_kt`, `visibility`, `altimeter_hpa`, `flight_category`, `cover`, `raw_metar`, `observed_at`, `collected_at`, `visibility_raw`

Note: wind_dir_deg is stored as a string.

</details>

<details><summary><code>silver/weather_forecast/data.parquet</code></summary>

`station_icao`, `timestamp`, `is_forecast`, `temperature_c`, `humidity_pct`, `precipitation_mm`, `wind_speed_ms`, `wind_direction_deg`, `weather_code`, `condition`, `wind_unit`, `source`, `collected_at`, `ingestion_date`

</details>

<details><summary><code>silver/weather_taf/data.parquet</code></summary>

`station_icao`, `issue_time`, `valid_from`, `valid_to`, `raw_taf`, `source`, `collected_at`

</details>

<details><summary><code>silver/fuel/data.parquet</code></summary>

`date`, `region`, `price_per_litre`, `currency`, `source`, `collected_at`, `ingestion_date`, `series_key`

</details>

<details><summary><code>silver/routes/data.parquet</code></summary>

`airline`, `origin`, `destination`, `stops`, `equipment`, `distance_km`, `source`, `collected_at`, `ingestion_date`, `_kind`, `id`

</details>

<details><summary><code>silver/airports/airports.parquet</code></summary>

`ident`, `name`, `type`, `latitude_deg`, `longitude_deg`, `elevation_ft`, `iso_country`, `municipality`, `iata_code`, `score`, `scheduled_service`, `ingestion_date`

</details>

<details><summary><code>silver/holidays/data.parquet</code></summary>

`country`, `date`, `name`, `source`, `collected_at`, `ingestion_date`, `_kind`, `id`

</details>

<details><summary><code>silver/aircraft/data.parquet</code></summary>

`type_iata`, `type_icao`, `manufacturer`, `family`, `engine`, `capacity`, `range_km`, `source`, `collected_at`, `ingestion_date`, `_kind`, `id`

</details>

<details><summary><code>silver/notams/data.parquet</code></summary>

`notam_id`, `icao_location`, `notam_type`, `message`, `qualification`, `valid_from`, `valid_to`, `source`, `collected_at`, `ingestion_date`

</details>

<details><summary><code>silver/emissions/data.parquet</code></summary>

`aircraft_type`, `fuel_burn_liters_per_hour`, `fuel_burn_kg_per_hour`, `co2_kg_per_hour`, `co2_tonnes_per_hour`, `emission_factor_kg_per_kg_fuel`, `fuel_density_kg_per_liter`, `source`, `collected_at`, `ingestion_date`, `_kind`, `id`

</details>

## Data sources

- Positions: ADS-B via adsb.lol, with airplanes.live and OpenSky fallbacks.
- Movements and delays: OpenSky (`/flights/*`) plus AirLabs schedules.
- Weather: METAR/TAF from aviationweather.gov, forecasts from Open-Meteo.
- Reference: airports, routes, aircraft, emissions and holidays built from bundled reference data.
- Official passengers: Eurostat `avia_paoa` (monthly, ~2 months behind), loaded as a benchmark layer.

## Reproducibility

- Bronze files are immutable and timestamped; Silver files are snapshots of the latest state.
- Local paths mirror the repo: `warehouse/bronze/<source>/…`, `warehouse/silver/<source>/…`.
- Regenerate locally with `uv run python -m scripts.lake_sync pull-silver`, then `uv run pytest`; see `docs/huggingface-dataset.md` for the scripts and cadence.

## License

MIT — same license as the [eu-air-traffic](https://github.com/swadhinbiswas/eu-air-traffic) repository.

## Citation

```bibtex
@misc{swadhinbiswas_air_traffic_lake,
  author = {Swadhin Biswas},
  title = {EU Air Traffic Lake},
  year = {2026},
  publisher = {Hugging Face},
  url = {https://huggingface.co/datasets/swadhinbiswas/air-traffic}
}
```

## Limitations

- OpenSky movements carry no schedule, so delay fields are null there; use AirLabs schedules or Gold delay marts for punctuality.
- Arrivals are backfilled nightly; same-day arrival coverage lags.
- METAR wind direction is a string in the source feed (`wind_dir_deg`), not numeric.
- TAF validity bounds are epoch integers, not ISO timestamps.
- This dataset mirrors the live pipeline: expect drifting schemas over months, pinned by Silver snapshots.

