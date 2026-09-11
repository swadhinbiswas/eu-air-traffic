{{ config(materialized='ephemeral', tags=['intermediate']) }}

with flights as (
    select * from {{ ref('stg_fact_flights') }}
),

airports as (
    select * from {{ ref('stg_dim_airport') }}
),

emission_estimates as (
    select
        f.flight_id,
        f.departure_icao,
        f.arrival_icao,
        f.airline_icao,
        dep.latitude_deg as dep_lat,
        dep.longitude_deg as dep_lon,
        arr.latitude_deg as arr_lat,
        arr.longitude_deg as arr_lon,
        2 * 6371 * asin(sqrt(
            power(sin(radians(arr.latitude_deg - dep.latitude_deg) / 2), 2) +
            cos(radians(dep.latitude_deg)) * cos(radians(arr.latitude_deg)) *
            power(sin(radians(arr.longitude_deg - dep.longitude_deg) / 2), 2)
        )) as estimated_distance_km,
        -- Default emission estimate: ~2.5 kg CO2 per kg fuel, ~0.8 kg/L fuel density
        -- Assume average fuel burn ~25 L/km for medium aircraft
        2 * 6371 * asin(sqrt(
            power(sin(radians(arr.latitude_deg - dep.latitude_deg) / 2), 2) +
            cos(radians(dep.latitude_deg)) * cos(radians(arr.latitude_deg)) *
            power(sin(radians(arr.longitude_deg - dep.longitude_deg) / 2), 2)
        )) * 25 * 0.8 * 2.52 as estimated_co2_kg
    from flights f
    left join airports dep on f.departure_icao = dep.airport_icao
    left join airports arr on f.arrival_icao = arr.airport_icao
)

select * from emission_estimates
