{{ config(materialized='view', tags=['staging']) }}

with source as (
    select * from {{ source('eurostat', 'eurostat_airport_traffic') }}
)

select
    period,
    cast(period || '-01' as date) as period_start,
    airport_icao,
    cast(passengers as bigint) as passengers,
    source,
    cast(fetched_at as timestamp) as fetched_at
from source
where period is not null
  and airport_icao is not null
