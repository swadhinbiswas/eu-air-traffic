{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'dim_airport') }}
),

renamed as (
    select
        airport_icao,
        iata_code,
        name,
        type,
        latitude_deg,
        longitude_deg,
        elevation_ft,
        iso_country,
        municipality,
        score
    from source
    where airport_icao is not null
)

select * from renamed
