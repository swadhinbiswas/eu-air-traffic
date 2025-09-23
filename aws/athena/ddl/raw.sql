-- Raw landing-zone tables. The Glue Crawler (stage 3) normally creates these
-- from the objects the Lambda lands, so this file is the definition for when
-- you want the shapes pinned, or a quick manual setup without a crawler run.
--
-- Layout the crawler sees:
--   s3://<raw-bucket>/adsb/dt=YYYY-MM-DD/…
--                       /opensky/…
--                       /airlabs/…
--                       /metar/…
--                       /eurostat/…

CREATE DATABASE IF NOT EXISTS eu_air_traffic_raw
COMMENT 'Raw landing zone written by the bronze-export Lambda';

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_raw.opensky_fact_flights (
  flight_id      string,
  callsign       string,
  departure_icao string,
  arrival_icao   string,
  status         string,
  delay_minutes  double,
  collected_at   timestamp
)
PARTITIONED BY (dt string)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_RAW_BUCKET/opensky/';

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_raw.airlabs_fact_schedules (
  flight_id         string,
  callsign          string,
  departure_icao    string,
  arrival_icao      string,
  scheduled_departure timestamp,
  scheduled_arrival   timestamp,
  delay_minutes     double
)
PARTITIONED BY (dt string)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_RAW_BUCKET/airlabs/';

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_raw.adsb_fact_positions (
  icao24        string,
  callsign      string,
  latitude      double,
  longitude     double,
  baro_altitude double,
  velocity      double,
  on_ground     boolean,
  timestamp     timestamp
)
PARTITIONED BY (dt string)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_RAW_BUCKET/adsb/';

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_raw.metar_weather (
  station_icao  string,
  timestamp     timestamp,
  temperature_c double,
  wind_speed_kt double,
  visibility_m  double,
  condition     string
)
PARTITIONED BY (dt string)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_RAW_BUCKET/metar/';

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_raw.eurostat_fact_airport_official (
  airport_icao string,
  year         int,
  month        int,
  passengers   bigint
)
PARTITIONED BY (dt string)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_RAW_BUCKET/eurostat/';

-- With the crawler, no manual partition load is needed. Without it:
--   MSCK REPAIR TABLE eu_air_traffic_raw.opensky_fact_flights;
