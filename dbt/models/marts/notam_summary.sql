{{ config(materialized='view', tags=['marts']) }}

with notams as (
    select * from {{ ref('stg_fact_notams') }}
),

summary as (
    select
        icao_location,
        notam_type,
        count(notam_id) as notam_count,
        max(collected_at) as latest_notam,
        min(valid_from) as earliest_valid_from,
        max(valid_to) as latest_valid_to
    from notams
    group by icao_location, notam_type
)

select *
from summary
order by notam_count desc
