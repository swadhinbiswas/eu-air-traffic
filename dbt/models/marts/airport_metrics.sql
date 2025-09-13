{{ config(materialized='view', tags=['marts']) }}

with flight_details as (
    select * from {{ ref('int_flight_details') }}
),

airport_metrics as (
    select
        departure_icao as airport_icao,
        departure_airport_name as airport_name,
        departure_country as country,
        count(*) as total_flights,
        round(avg(delay_minutes), 2) as avg_delay_minutes,
        max(delay_minutes) as max_delay_minutes,
        round(avg(case when is_on_time is null then null when is_on_time then 1.0 else 0.0 end), 4) as on_time_rate,
        sum(case when status = 'cancelled' then 1 else 0 end) as cancelled_flights,
        round(avg(flight_duration_minutes), 1) as avg_flight_duration_min
    from flight_details
    -- Arrival-only movements have no departure; they are counted by their
    -- arrival airport instead of producing a NULL leaderboard row.
    where departure_icao is not null
    group by departure_icao, departure_airport_name, departure_country
)

select *
from airport_metrics
order by total_flights desc
