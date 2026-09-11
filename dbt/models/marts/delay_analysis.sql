{{ config(materialized='view', tags=['marts']) }}

with flight_details as (
    select * from {{ ref('int_delay_calculations') }}
),

delay_by_status as (
    select
        status,
        count(*) as flight_count,
        round(avg(delay_minutes), 2) as avg_delay_minutes,
        min(delay_minutes) as min_delay_minutes,
        max(delay_minutes) as max_delay_minutes
    from flight_details
    group by status
)

select *
from delay_by_status
order by flight_count desc
