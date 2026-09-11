{{ config(materialized='view', tags=['marts']) }}

with emissions as (
    select * from {{ ref('stg_fact_emissions') }}
)

select
    aircraft_type,
    fuel_burn_liters_per_hour,
    fuel_burn_kg_per_hour,
    co2_kg_per_hour,
    co2_tonnes_per_hour,
    emission_factor_kg_per_kg_fuel
from emissions
order by co2_kg_per_hour desc
