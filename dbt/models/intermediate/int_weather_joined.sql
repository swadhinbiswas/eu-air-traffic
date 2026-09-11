{{ config(materialized='ephemeral', tags=['intermediate']) }}

with flights as (
    select * from {{ ref('stg_fact_flights') }}
),

weather as (
    select * from {{ ref('stg_weather') }}
),

-- Join weather to flights based on arrival airport and hour
weather_joined as (
    select
        f.flight_id,
        f.departure_icao,
        f.arrival_icao,
        f.scheduled_arrival,
        f.delay_minutes,
        f.status,
        w.station_icao as weather_station,
        w.temperature_c,
        w.wind_speed_ms,
        w.visibility_km,
        w.condition as weather_condition
    from flights f
    left join weather w
        on f.arrival_icao = w.station_icao
        and date_trunc('hour', f.scheduled_arrival) = date_trunc('hour', w.timestamp)
)

select * from weather_joined
