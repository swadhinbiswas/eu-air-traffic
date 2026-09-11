{{ config(materialized='view', tags=['marts']) }}

with source as (
    select * from {{ source('raw_warehouse', 'dim_fuel') }}
)

select
    date,
    region,
    price_per_litre,
    currency
from source
where date is not null
  and region is not null
order by date desc, region
