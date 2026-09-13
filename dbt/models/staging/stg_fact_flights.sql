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
        -- Sources have used en_route/enroute historically; map to one canonical
        -- vocabulary. An unrecognised value becomes NULL rather than a wrong
        -- state.
        case
            when status is null then null
            when lower(trim(status)) in ('en_route', 'enroute', 'en route') then 'en-route'
            when lower(trim(status)) in ('landed', 'scheduled', 'cancelled', 'diverted', 'active', 'en-route')
                then lower(trim(status))
            else null
        end as status,
        -- NULL means "no schedule was available" (OpenSky movements), which is
        -- different from "on time". Punctuality averages must ignore it.
        delay_minutes,
        coalesce(cancelled, false) as cancelled,
        source as data_source,
        -- Live rows arrive without ingestion_date; derive it from collected_at
        -- so the freshness report has a value for every layer.
        coalesce(ingestion_date, substr(cast(collected_at as varchar), 1, 10)) as ingestion_date,
        collected_at
    from source
    where flight_id is not null
)

select * from renamed
