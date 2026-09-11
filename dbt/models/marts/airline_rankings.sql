{{ config(materialized='view', tags=['marts']) }}

with flight_details as (
    select * from {{ ref('int_flight_details') }}
),

airline_stats as (
    select
        airline_icao,
        airline_name,
        count(*) as total_flights,
        round(avg(delay_minutes), 2) as avg_delay_minutes,
        round(avg(case when is_on_time then 1.0 else 0.0 end), 4) as on_time_rate,
        sum(case when status = 'cancelled' then 1 else 0 end) as cancelled_flights,
        count(distinct departure_icao) as unique_departure_airports,
        count(distinct arrival_icao) as unique_arrival_airports
    from flight_details
    where airline_icao is not null
    group by airline_icao, airline_name
),

ranked as (
    select
        *,
        row_number() over (order by avg_delay_minutes asc) as rank,
        round(avg_delay_minutes - lag(avg_delay_minutes) over (order by avg_delay_minutes), 2) as delay_gap_from_previous
    from airline_stats
)

select *
from ranked
order by rank
