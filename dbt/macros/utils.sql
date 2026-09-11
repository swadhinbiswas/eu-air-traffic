{#
  Utility macros for the air_traffic dbt project.
#}

{% macro calculate_on_time_rate(delay_column, threshold=15) %}
    {#
      Calculate the on-time performance rate.

      Args:
        delay_column: The column containing delay in minutes
        threshold: Maximum delay to be considered "on time" (default: 15)

      Returns:
        SQL expression that calculates the rate
    #}
    round(
        avg(case when {{ delay_column }} <= {{ threshold }} then 1.0 else 0.0 end),
        4
    )
{% endmacro %}


{% macro calculate_delay_rate(delay_column, threshold=15) %}
    {#
      Calculate the delay rate (percentage of flights delayed beyond threshold).

      Args:
        delay_column: The column containing delay in minutes
        threshold: Delay threshold in minutes (default: 15)

      Returns:
        SQL expression that calculates the rate
    #}
    round(
        avg(case when {{ delay_column }} > {{ threshold }} then 1.0 else 0.0 end),
        4
    )
{% endmacro %}


{% macro icao_to_country(icao_code) %}
    {#
      Convert ICAO code prefix to country code.

      Args:
        icao_code: ICAO code (airport or airline)

      Returns:
        SQL expression that returns the country code
    #}
    case left(upper({{ icao_code }}), 2)
        when 'EG' then 'GB'
        when 'ED' then 'DE'
        when 'ET' then 'DE'
        when 'LF' then 'FR'
        when 'EH' then 'NL'
        when 'LE' then 'ES'
        when 'GC' then 'ES'
        when 'LI' then 'IT'
        when 'LS' then 'CH'
        when 'LO' then 'AT'
        when 'EK' then 'DK'
        when 'EN' then 'NO'
        when 'ES' then 'SE'
        when 'EF' then 'FI'
        when 'EP' then 'PL'
        when 'LK' then 'CZ'
        when 'LZ' then 'SK'
        when 'LH' then 'HU'
        when 'LB' then 'BG'
        when 'LR' then 'RO'
        when 'LU' then 'MD'
        when 'LG' then 'GR'
        when 'LC' then 'CY'
        when 'LA' then 'HR'
        when 'LD' then 'SI'
        when 'LJ' then 'BA'
        when 'LY' then 'RS'
        when 'LW' then 'MK'
        when 'BI' then 'IS'
        when 'EI' then 'IE'
        when 'EB' then 'BE'
        when 'EL' then 'LU'
        when 'EV' then 'LV'
        when 'EY' then 'LT'
        when 'EE' then 'EE'
        when 'LP' then 'PT'
        when 'LM' then 'MT'
        else left(upper({{ icao_code }}), 2)
    end
{% endmacro %}


{% macro calculate_distance_km(lat1, lon1, lat2, lon2) %}
    {#
      Calculate distance between two points using Haversine formula.

      Args:
        lat1, lon1: Latitude and longitude of point 1
        lat2, lon2: Latitude and longitude of point 2

      Returns:
        SQL expression that returns distance in kilometers
    #}
    2 * 6371 * asin(sqrt(
        power(sin(radians({{ lat2 }} - {{ lat1 }}) / 2), 2) +
        cos(radians({{ lat1 }})) * cos(radians({{ lat2 }})) *
        power(sin(radians({{ lon2 }} - {{ lon1 }}) / 2), 2)
    ))
{% endmacro %}


{% macro classify_delay_category(delay_column) %}
    {#
      Classify delay into categories.

      Args:
        delay_column: The column containing delay in minutes

      Returns:
        SQL expression that returns the delay category
    #}
    case
        when {{ delay_column }} is null then 'unknown'
        when {{ delay_column }} <= 0 then 'early_or_on_time'
        when {{ delay_column }} <= 15 then 'minor_delay'
        when {{ delay_column }} <= 60 then 'moderate_delay'
        when {{ delay_column }} <= 180 then 'significant_delay'
        else 'severe_delay'
    end
{% endmacro %}


{% macro format_co2(co2_kg_column) %}
    {#
      Format CO2 values with appropriate unit.

      Args:
        co2_kg_column: Column containing CO2 in kg

      Returns:
        SQL expression that returns formatted string
    #}
    case
        when {{ co2_kg_column }} >= 1000
            then round({{ co2_kg_column }} / 1000, 2) || ' tonnes'
        else round({{ co2_kg_column }}, 2) || ' kg'
    end
{% endmacro %}
