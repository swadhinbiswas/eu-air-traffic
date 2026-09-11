{{ config(materialized='view', tags=['marts']) }}

with flight_details as (
    select * from {{ ref('int_flight_details') }}
),

trends as (
    select
        flight_date,
        departure_hour as hour_of_day,
        count(*) as flight_count,
        round(avg(delay_minutes), 2) as avg_delay_minutes,
        round(avg(case when is_on_time then 1.0 else 0.0 end), 4) as on_time_rate,
        sum(case when status = 'cancelled' then 1 else 0 end) as cancelled_flights
    from flight_details
    where flight_date is not null
    group by flight_date, departure_hour
)

select *
from trends
order by flight_date desc, hour_of_day
