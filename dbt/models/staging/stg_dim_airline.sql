{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'dim_airline') }}
),

renamed as (
    select
        airline_icao,
        airline_name
    from source
    where airline_icao is not null
)

select * from renamed
