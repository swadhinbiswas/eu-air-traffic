{{ config(materialized='ephemeral', tags=['intermediate']) }}

with flights as (
    select * from {{ ref('stg_fact_flights') }}
),

airports as (
    select * from {{ ref('stg_dim_airport') }}
),

airlines as (
    select * from {{ ref('stg_dim_airline') }}
),

enriched as (
    select
        f.*,
        dep.name as departure_airport_name,
        dep.iata_code as departure_iata,
        dep.latitude_deg as departure_lat,
        dep.longitude_deg as departure_lon,
        dep.iso_country as departure_country,
        arr.name as arrival_airport_name,
        arr.iata_code as arrival_iata,
        arr.latitude_deg as arrival_lat,
        arr.longitude_deg as arrival_lon,
        arr.iso_country as arrival_country,
        al.airline_name,
        epoch(f.actual_arrival - f.actual_departure) / 60 as flight_duration_minutes,
        case
            when f.delay_minutes is null then null
            when f.delay_minutes > {{ var('delay_threshold_minutes') }} then true
            else false
        end as is_delayed,
        case
            when f.delay_minutes is null then null
            when f.delay_minutes <= {{ var('on_time_threshold_minutes') }} then true
            else false
        end as is_on_time,
        coalesce(f.scheduled_departure, f.actual_departure) as reference_departure,
        cast(coalesce(f.scheduled_departure, f.actual_departure) as date) as flight_date,
        hour(coalesce(f.scheduled_departure, f.actual_departure)) as departure_hour,
        dayofweek(coalesce(f.scheduled_departure, f.actual_departure)) as departure_day_of_week
    from flights f
    left join airports dep on f.departure_icao = dep.airport_icao
    left join airports arr on f.arrival_icao = arr.airport_icao
    left join airlines al on f.airline_icao = al.airline_icao
),

keyed as (
    -- One real flight can appear twice: an OpenSky movement (actual times) and
    -- an AirLabs schedule (planned times + delay). Key them to the same real
    -- flight so every count and average sees it once.
    select
        *,
        upper(trim(coalesce(callsign, flight_id)))
            || '|' || coalesce(cast(flight_date as varchar), flight_id)
            || '|' || coalesce(departure_icao, arrival_icao, '') as flight_key
    from enriched
),

deduped as (
    select * exclude (rank_in_key)
    from (
        select
            *,
            row_number() over (
                partition by flight_key
                order by
                    (data_source = 'airlabs') desc,
                    (delay_minutes is not null) desc,
                    actual_arrival desc nulls last,
                    flight_id
            ) as rank_in_key
        from keyed
    )
    where rank_in_key = 1
)

select * from deduped
