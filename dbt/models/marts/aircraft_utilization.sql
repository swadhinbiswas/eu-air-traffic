{{ config(materialized='view', tags=['marts']) }}

with aircraft as (
    select * from {{ ref('stg_dim_aircraft') }}
),

routes as (
    select
        equipment as type_icao,
        count(*) as routes_served,
        round(avg(distance_km), 1) as avg_route_distance_km
    from {{ ref('stg_dim_route') }}
    group by equipment
),

emissions as (
    select * from {{ ref('stg_fact_emissions') }}
)

select
    a.type_icao,
    a.manufacturer,
    a.family,
    a.engine,
    a.capacity,
    a.range_km,
    coalesce(r.routes_served, 0) as routes_served,
    r.avg_route_distance_km,
    e.co2_kg_per_hour,
    e.fuel_burn_liters_per_hour
from aircraft a
left join routes r on r.type_icao = a.type_icao
left join emissions e on e.aircraft_type = a.type_icao
order by a.capacity desc
