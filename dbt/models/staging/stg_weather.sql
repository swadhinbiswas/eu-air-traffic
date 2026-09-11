{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'weather') }}
),

renamed as (
    select
        station_icao,
        timestamp,
        temperature_c,
        coalesce(temperature_c, 0) as temperature_c_safe,
        wind_speed_ms,
        -- visibility_m is numeric metres for OpenWeather and normalised METAR,
        -- but historical rows may hold raw strings ("6+"): cast defensively.
        try_cast(visibility_m as double) / 1000.0 as visibility_km,
        humidity_pct,
        pressure_hpa,
        condition,
        -- Some live rows carry only collected_at; derive the partition date.
        coalesce(
            try_cast(ingestion_date as date),
            try_cast(collected_at as date)
        ) as ingestion_date
    from source
)

select * from renamed
