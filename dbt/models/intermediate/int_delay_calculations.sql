{{ config(materialized='ephemeral', tags=['intermediate']) }}

with flights as (
    select * from {{ ref('stg_fact_flights') }}
),

delay_stats as (
    select
        flight_id,
        departure_icao,
        arrival_icao,
        airline_icao,
        scheduled_departure,
        scheduled_arrival,
        actual_departure,
        actual_arrival,
        delay_minutes,
        status,
        -- Delay categories
        case
            when status = 'cancelled' then 'cancelled'
            when delay_minutes <= 0 then 'early_or_on_time'
            when delay_minutes <= 15 then 'minor_delay'
            when delay_minutes <= 60 then 'moderate_delay'
            when delay_minutes <= 180 then 'significant_delay'
            else 'severe_delay'
        end as delay_category,
        -- Calculate scheduled vs actual difference in minutes
        epoch(actual_departure - scheduled_departure) / 60 as departure_delay_min,
        epoch(actual_arrival - scheduled_arrival) / 60 as arrival_delay_min
    from flights
    where actual_departure is not null
)

select * from delay_stats
