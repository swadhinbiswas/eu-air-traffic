{#
  Use custom schema names verbatim instead of dbt's default
  `<target_schema>_<custom_schema>` concatenation.

  This keeps the physical schemas predictable (`staging`, `marts`, `reports`)
  so downstream consumers — e.g. the orchestrator registering `gold_*` views
  from the `marts` schema — can rely on them.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}

