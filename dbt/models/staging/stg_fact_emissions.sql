{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'fact_emissions') }}
),

renamed as (
    select
        aircraft_type,
        fuel_burn_liters_per_hour,
        fuel_burn_kg_per_hour,
        co2_kg_per_hour,
        co2_tonnes_per_hour,
        emission_factor_kg_per_kg_fuel,
        fuel_density_kg_per_liter,
        source as data_source,
        collected_at
    from source
    where aircraft_type is not null
)

select * from renamed
