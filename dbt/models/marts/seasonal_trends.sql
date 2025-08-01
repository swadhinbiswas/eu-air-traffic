{{ config(materialized='view') }}

with fact_flights as (
    select * from {{ ref('stg_fact_flights') }}
),

hourly as (
    select
        cast(scheduled_departure as date) as flight_date,
        date_part('hour', scheduled_departure) as hour_of_day,
        count(*) as flight_count,
        round(avg(delay_minutes), 2) as avg_delay_minutes
    from fact_flights
    group by 1, 2
)

select *
from hourly
order by flight_date, hour_of_day
