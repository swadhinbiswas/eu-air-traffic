{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'fact_flights') }}
),

renamed as (
    select
        flight_id,
        callsign,
        trim(callsign) as callsign_clean,
        airline_icao,
        departure_icao,
        arrival_icao,
        scheduled_departure,
        scheduled_arrival,
        actual_departure,
        actual_arrival,
        status,
        -- NULL means "no schedule was available" (OpenSky movements), which is
        -- different from "on time". Punctuality averages must ignore it.
        delay_minutes,
        coalesce(cancelled, false) as cancelled,
        source as data_source,
        ingestion_date
    from source
    where flight_id is not null
)

select * from renamed
