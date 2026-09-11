{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'dim_aircraft') }}
),

renamed as (
    select
        type_icao,
        type_iata,
        manufacturer,
        family,
        engine,
        capacity,
        range_km
    from source
    where type_icao is not null
)

select * from renamed
