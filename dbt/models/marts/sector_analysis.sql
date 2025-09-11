{{ config(materialized='view', tags=['marts']) }}

with flights as (
    select * from {{ ref('stg_fact_flights') }}
),

sector_stats as (
    select
        departure_icao as sector_code,
        cast(scheduled_departure as date) as date,
        count(flight_id) as flight_count,
        round(avg(delay_minutes), 2) as avg_delay_minutes,
        round(
            avg(case when delay_minutes <= {{ var('on_time_threshold_minutes') }} then 1.0 else 0.0 end), 4
        ) as on_time_rate
    from flights
    where scheduled_departure is not null
    group by departure_icao, cast(scheduled_departure as date)
)

select *
from sector_stats
order by date desc, flight_count desc
