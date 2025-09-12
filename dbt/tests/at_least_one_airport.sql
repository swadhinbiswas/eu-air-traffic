-- OpenSky usually knows only one end of a flight movement, but a record with
-- neither airport is useless. Fail if any flight has both ends missing.
select
    flight_id
from {{ ref('stg_fact_flights') }}
where departure_icao is null
  and arrival_icao is null
