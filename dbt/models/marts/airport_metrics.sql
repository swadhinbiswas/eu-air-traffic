{{ config(materialized='view') }}

with fact_flights as (
    select * from {{ ref('stg_fact_flights') }}
),

airport_metrics as (
    select
        departure_icao as airport_icao,
        count(*) as total_flights,
        round(avg(delay_minutes), 2) as avg_delay_minutes,
        max(delay_minutes) as max_delay_minutes,
        round(avg(case when delay_minutes <= 15 then 1.0 else 0.0 end), 4) as on_time_rate
    from fact_flights
    where status != 'cancelled'
    group by departure_icao
)

select *
from airport_metrics
order by total_flights desc
