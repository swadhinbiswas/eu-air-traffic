{{ config(materialized='view') }}

with fact_flights as (
    select * from {{ ref('stg_fact_flights') }}
),

airlines as (
    select airline_icao, airline_name from main.dim_airline
),

ranked as (
    select
        f.airline_icao,
        a.airline_name,
        count(*) as total_flights,
        round(avg(f.delay_minutes), 2) as avg_delay_minutes,
        round(avg(case when f.delay_minutes <= 15 then 1.0 else 0.0 end), 4) as on_time_rate
    from fact_flights f
    left join airlines a on f.airline_icao = a.airline_icao
    where f.status != 'cancelled'
    group by f.airline_icao, a.airline_name
)

select
    airline_icao,
    airline_name,
    total_flights,
    avg_delay_minutes,
    on_time_rate,
    row_number() over (order by avg_delay_minutes asc) as rank
from ranked
