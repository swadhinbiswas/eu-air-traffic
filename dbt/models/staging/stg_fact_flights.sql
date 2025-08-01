{{ config(materialized='view') }}

select *
from main.fact_flights
