{{ config(materialized='view', tags=['marts']) }}

with weather_joined as (
    select * from {{ ref('int_weather_joined') }}
),

impact as (
    select
        weather_condition,
        count(flight_id) as flight_count,
        round(avg(delay_minutes), 2) as avg_delay_minutes,
        round(avg(temperature_c), 1) as avg_temperature_c,
        round(avg(wind_speed_ms), 1) as avg_wind_speed_ms,
        round(avg(case when delay_minutes > {{ var('delay_threshold_minutes') }} then 1.0 else 0.0 end), 4) as delay_rate
    from weather_joined
    where weather_condition is not null
    group by weather_condition
)

select *
from impact
order by flight_count desc
