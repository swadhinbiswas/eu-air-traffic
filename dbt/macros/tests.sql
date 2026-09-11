{#
  Custom test macros for the air_traffic dbt project.
#}

{% test assert_positive_values(model, column_name) %}
    {#
      Test that a column contains only positive values.
    #}
    select *
    from {{ model }}
    where {{ column_name }} <= 0
      and {{ column_name }} is not null
{% endtest %}


{% test assert_valid_coordinates(model, lat_column, lon_column) %}
    {#
      Test that latitude and longitude values are within valid ranges.
    #}
    select *
    from {{ model }}
    where {{ lat_column }} < -90 or {{ lat_column }} > 90
       or {{ lon_column }} < -180 or {{ lon_column }} > 180
{% endtest %}


{% test assert_valid_delay_range(model, column_name) %}
    {#
      Test that delay values are within a reasonable range.
    #}
    select *
    from {{ model }}
    where {{ column_name }} < 0
       or {{ column_name }} > 1440
{% endtest %}


{% test assert_valid_percentage(model, column_name) %}
    {#
      Test that a column contains valid percentages (0 to 1).
    #}
    select *
    from {{ model }}
    where {{ column_name }} < 0
       or {{ column_name }} > 1
{% endtest %}


{% test assert_valid_temperature(model, column_name) %}
    {#
      Test that temperature values are within a realistic range for aviation.
    #}
    select *
    from {{ model }}
    where {{ column_name }} < -80
       or {{ column_name }} > 60
{% endtest %}


{% test assert_valid_wind_speed(model, column_name) %}
    {#
      Test that wind speed values are within a realistic range.
    #}
    select *
    from {{ model }}
    where {{ column_name }} < 0
       or {{ column_name }} > 100
{% endtest %}
