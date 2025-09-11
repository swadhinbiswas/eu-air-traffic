{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'fact_positions') }}
),

renamed as (
    select
        icao24,
        callsign,
        registration,
        aircraft_type,
        coalesce(aircraft_class, 'other') as aircraft_class,
        coalesce(emitter_class, 'unknown') as emitter_class,
        coalesce(is_cargo, false) as is_cargo,
        coalesce(is_military, false) as is_military,
        operator_name,
        operator_country,
        operator_category,
        type_name,
        manufacturer,
        airframe,
        wake_category,
        latitude,
        longitude,
        altitude,
        velocity,
        heading,
        vertical_rate,
        squawk,
        emergency,
        co2_kg_per_hour,
        fuel_burn_kg_per_hour,
        source as data_source,
        collected_at
    from source
    where icao24 is not null
)

select * from renamed
