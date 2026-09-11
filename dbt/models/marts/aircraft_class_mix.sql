{{ config(materialized='view', tags=['marts']) }}

-- Operational mix of the live European airspace: how many aircraft of each
-- class are airborne, plus their estimated carbon intensity. This is the mart
-- behind the dashboard's "operational class mix" and "carbon intensity" panels.

with positions as (
    select * from {{ ref('stg_fact_positions') }}
)

select
    aircraft_class,
    count(*) as aircraft,
    round(avg(altitude), 0) as avg_altitude_ft,
    round(avg(velocity), 0) as avg_ground_speed_kt,
    round(sum(co2_kg_per_hour), 1) as total_co2_kg_per_hour,
    round(avg(co2_kg_per_hour), 1) as avg_co2_kg_per_hour,
    sum(case when is_cargo then 1 else 0 end) as cargo_aircraft,
    sum(case when is_military then 1 else 0 end) as military_aircraft
from positions
group by aircraft_class
order by aircraft desc
