# Superset image extended with the DuckDB SQLAlchemy driver.
#
# The official `apache/superset` lean images ship without database drivers,
# so we add `duckdb-engine` here so dashboards can query the shared warehouse.

FROM apache/superset:4.1.1

USER root

RUN pip install --no-cache-dir --no-warn-script-location \
        duckdb==1.5.4 \
        duckdb-engine==0.14.2 \
    && rm -rf /root/.cache/pip

USER superset
