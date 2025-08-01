{{ config(materialized='view') }}

select *
from main.weather
