-- Curated-zone tables for Amazon Athena (stage 6 of the AWS version).
--
-- The Glue ETL job (aws/glue/etl_job.py) writes Parquet to
--   s3://<curated-bucket>/curated/{fact,dim,aggregates}/<table>/date=YYYY-MM-DD/
-- and updates the Glue Data Catalog, so these tables usually appear on their
-- own. This file is the explicit definition — run it if you are creating the
-- tables by hand or want to pin the column types the BI layer depends on.

CREATE DATABASE IF NOT EXISTS eu_air_traffic_curated
COMMENT 'Curated zone: clean, deduplicated air-traffic data for BI';

-- ── Facts ────────────────────────────────────────────────────────────────────

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_curated.fact_flights (
  flight_id         string,
  callsign          string,
  departure_icao    string,
  arrival_icao      string,
  status            string,
  delay_minutes     double,
  source            string,
  collected_at      timestamp
)
PARTITIONED BY (date date)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_CURATED_BUCKET/curated/fact/fact_flights/'
TBLPROPERTIES ('parquet.compression' = 'ZSTD');

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_curated.fact_schedules (
  flight_id         string,
  callsign          string,
  departure_icao    string,
  arrival_icao      string,
  scheduled_departure timestamp,
  scheduled_arrival   timestamp,
  delay_minutes     double,
  source            string
)
PARTITIONED BY (date date)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_CURATED_BUCKET/curated/fact/fact_schedules/'
TBLPROPERTIES ('parquet.compression' = 'ZSTD');

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_curated.fact_positions (
  icao24            string,
  callsign          string,
  latitude          double,
  longitude         double,
  baro_altitude     double,
  velocity          double,
  vertical_rate     double,
  on_ground         boolean,
  timestamp         timestamp
)
PARTITIONED BY (date date)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_CURATED_BUCKET/curated/fact/fact_positions/'
TBLPROPERTIES ('parquet.compression' = 'ZSTD');

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_curated.weather (
  station_icao      string,
  timestamp         timestamp,
  temperature_c     double,
  wind_speed_kt     double,
  visibility_m      double,
  condition         string
)
PARTITIONED BY (date date)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_CURATED_BUCKET/curated/fact/weather/'
TBLPROPERTIES ('parquet.compression' = 'ZSTD');

-- Eurostat official monthly passengers — the benchmark the platform checks
-- itself against. Two-month publication lag, so daily partitions are ample.
CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_curated.fact_airport_official (
  airport_icao      string,
  year              int,
  month             int,
  passengers        bigint,
  source            string
)
PARTITIONED BY (date date)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_CURATED_BUCKET/curated/fact/fact_airport_official/'
TBLPROPERTIES ('parquet.compression' = 'ZSTD');

-- ── Aggregates ───────────────────────────────────────────────────────────────

CREATE EXTERNAL TABLE IF NOT EXISTS eu_air_traffic_curated.daily_traffic (
  flights                   bigint,
  avg_delay_minutes         double,
  flights_with_known_delay  bigint
)
PARTITIONED BY (date date)
STORED AS PARQUET
LOCATION 's3://REPLACE_WITH_CURATED_BUCKET/curated/aggregates/daily_traffic/'
TBLPROPERTIES ('parquet.compression' = 'ZSTD');

-- Partitions are registered by the Glue job's catalog update. If you created
-- these tables by hand, load them once with:
--   MSCK REPAIR TABLE eu_air_traffic_curated.fact_flights;
