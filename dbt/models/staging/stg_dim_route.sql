{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'dim_route') }}
),

renamed as (
    select
        origin,
        destination,
        airline,
        stops,
        equipment,
        distance_km
    from source
    where origin is not null
      and destination is not null
)

select * from renamed
