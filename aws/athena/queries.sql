-- Sample Athena queries over the curated zone. These mirror the dashboard's
-- analytics pages, so the BI layer (QuickSight / Grafana) has a starting point.
-- Run them in the workgroup created by infra/terraform-serverless/athena.tf.

-- 1. Traffic and punctuality by day (the "is it current?" trend).
SELECT
  date,
  count(*)                                                   AS flights,
  round(avg(delay_minutes) FILTER (WHERE delay_minutes IS NOT NULL), 2)
                                                             AS avg_delay_minutes,
  round(
    count(*) FILTER (WHERE delay_minutes <= 15)::double
    / nullif(count(*) FILTER (WHERE delay_minutes IS NOT NULL), 0),
    4
  )                                                          AS on_time_rate
FROM eu_air_traffic_curated.fact_flights
GROUP BY date
ORDER BY date DESC
LIMIT 30;

-- 2. Busiest airports (departures and arrivals together).
WITH movements AS (
  SELECT departure_icao AS airport_icao FROM eu_air_traffic_curated.fact_flights
  UNION ALL
  SELECT arrival_icao   AS airport_icao FROM eu_air_traffic_curated.fact_flights
)
SELECT airport_icao, count(*) AS movements
FROM movements
WHERE airport_icao IS NOT NULL
GROUP BY airport_icao
ORDER BY movements DESC
LIMIT 20;

-- 3. Punctuality by airport, counting only flights with a known delay.
SELECT
  departure_icao AS airport_icao,
  count(*)                                            AS flights,
  round(avg(delay_minutes), 2)                        AS avg_delay_minutes,
  count(delay_minutes)                                AS flights_with_known_delay
FROM eu_air_traffic_curated.fact_flights
GROUP BY departure_icao
HAVING count(*) >= 20
ORDER BY avg_delay_minutes DESC
LIMIT 20;

-- 4. Weather impact: delay by weather condition at the departure station.
SELECT
  w.condition,
  count(*)                      AS flights,
  round(avg(f.delay_minutes), 2) AS avg_delay_minutes
FROM eu_air_traffic_curated.fact_flights f
JOIN eu_air_traffic_curated.weather w
  ON w.station_icao = f.departure_icao
 AND w.date = f.date
WHERE f.delay_minutes IS NOT NULL
GROUP BY w.condition
ORDER BY avg_delay_minutes DESC;

-- 5. Observed movements vs the Eurostat benchmark (the cross-check the
--    platform is built around). Eurostat lags by ~2 months, so compare months
--    that are fully observed.
SELECT
  f.year,
  f.month,
  f.airport_icao,
  f.passengers      AS eurostat_passengers,
  o.observed_flights
FROM eu_air_traffic_curated.fact_airport_official f
LEFT JOIN (
  SELECT
    year(date)  AS year,
    month(date) AS month,
    departure_icao AS airport_icao,
    count(*) AS observed_flights
  FROM eu_air_traffic_curated.fact_flights
  GROUP BY 1, 2, 3
) o
  ON o.year = f.year AND o.month = f.month AND o.airport_icao = f.airport_icao
ORDER BY f.year DESC, f.month DESC, f.passengers DESC;
