{{ config(materialized='view', tags=['marts']) }}

with flights as (
    select * from {{ ref('stg_fact_flights') }}
),

routes as (
    select * from {{ ref('stg_dim_route') }}
),

performance as (
    select
        r.origin,
        r.destination,
        r.airline,
        count(f.flight_id) as total_flights,
        round(avg(f.delay_minutes), 2) as avg_delay_minutes,
        round(
            avg(case when f.delay_minutes <= {{ var('on_time_threshold_minutes') }} then 1.0 else 0.0 end), 4
        ) as on_time_rate,
        avg(r.distance_km) as avg_distance_km
    from routes r
    left join flights f on f.departure_icao = r.origin and f.arrival_icao = r.destination
    group by r.origin, r.destination, r.airline
)

select *
from performance
order by total_flights desc
