{{ config(materialized='view', tags=['reports']) }}

with flight_details as (
    select * from {{ ref('int_flight_details') }}
),

daily_quality as (
    select
        flight_date,
        count(*) as total_flights,
        round(avg(delay_minutes), 2) as avg_delay_minutes,
        round(avg(case when is_on_time then 1.0 else 0.0 end), 4) as on_time_rate,
        sum(case when status = 'cancelled' then 1 else 0 end) as cancelled_count,
        round(
            sum(case when status = 'cancelled' then 1 else 0 end)::double / count(*)::double, 4
        ) as cancellation_rate,
        round(
            sum(case when delay_minutes > {{ var('delay_threshold_minutes') }} then 1 else 0 end)::double / count(*)::double, 4
        ) as delay_rate
    from flight_details
    where flight_date is not null
    group by flight_date
)

select *
from daily_quality
order by flight_date desc
