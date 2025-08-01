{{ config(materialized='view') }}

with fact_flights as (
    select * from {{ ref('stg_fact_flights') }}
),

weather as (
    select * from {{ ref('stg_weather') }}
),

flight_weather as (
    select
        f.flight_id,
        f.arrival_icao,
        f.delay_minutes,
        w.condition,
        w.temperature_c,
        w.wind_speed_ms,
        w.visibility_m
    from fact_flights f
    left join weather w
        on f.arrival_icao = w.station_icao
        and date_trunc('hour', f.scheduled_arrival) = date_trunc('hour', w.timestamp)
    where f.status != 'cancelled'
)

select
    condition as weather_condition,
    count(*) as flight_count,
    round(avg(delay_minutes), 2) as avg_delay_minutes,
    round(avg(temperature_c), 1) as avg_temperature_c,
    round(avg(wind_speed_ms), 1) as avg_wind_speed_ms
from flight_weather
where condition is not null
group by condition
order by flight_count desc
