{{ config(materialized='view', tags=['reports']) }}

with flights_freshness as (
    select
        'fact_flights' as table_name,
        max(cast(ingestion_date as date)) as last_loaded,
        now() - max(cast(ingestion_date as timestamp)) as staleness_interval,
        case
            when now() - max(cast(ingestion_date as timestamp)) > interval '24 hours' then 'STALE'
            when now() - max(cast(ingestion_date as timestamp)) > interval '12 hours' then 'WARNING'
            else 'FRESH'
        end as freshness_status
    from {{ ref('stg_fact_flights') }}
    -- Only report tables that actually hold data; an empty fact must not yield
    -- a NULL freshness row.
    having max(cast(ingestion_date as timestamp)) is not null
),

weather_freshness as (
    select
        'weather' as table_name,
        max(cast(ingestion_date as date)) as last_loaded,
        now() - max(cast(ingestion_date as timestamp)) as staleness_interval,
        case
            when now() - max(cast(ingestion_date as timestamp)) > interval '24 hours' then 'STALE'
            when now() - max(cast(ingestion_date as timestamp)) > interval '12 hours' then 'WARNING'
            else 'FRESH'
        end as freshness_status
    from {{ ref('stg_weather') }}
    having max(cast(ingestion_date as timestamp)) is not null
)

select * from flights_freshness
union all
select * from weather_freshness
