{{ config(materialized='view', tags=['marts']) }}

with official as (
    select * from {{ ref('stg_eurostat_airport_traffic') }}
),

airports as (
    select airport_icao, name as airport_name, iso_country as country from {{ ref('stg_dim_airport') }}
),

latest as (
    select max(period_start) as period_start from official
),

metrics as (
    select
        o.airport_icao,
        a.airport_name,
        a.country,
        max(o.period_start) as latest_month,
        sum(case when o.period_start >= l.period_start - interval '12 months' then o.passengers end)
            as passengers_12m,
        sum(case when o.period_start >= date_trunc('year', l.period_start) then o.passengers end)
            as passengers_ytd,
        count(distinct o.period) as months_reported
    from official o
    cross join latest l
    left join airports a on o.airport_icao = a.airport_icao
    group by 1, 2, 3
)

select
    airport_icao,
    airport_name,
    country,
    latest_month,
    passengers_12m,
    passengers_ytd,
    months_reported,
    row_number() over (order by passengers_12m desc nulls last) as official_rank
from metrics
where passengers_12m is not null
order by official_rank
