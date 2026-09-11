{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('raw_warehouse', 'fact_notams') }}
),

renamed as (
    select
        notam_id,
        icao_location,
        notam_type,
        message,
        qualification,
        valid_from,
        valid_to,
        source as data_source,
        collected_at
    from source
    where notam_id is not null
)

select * from renamed
