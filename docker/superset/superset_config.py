"""Superset configuration for the Air Traffic Platform.

Connects Superset to the DuckDB warehouse file via the duckdb SQLAlchemy
dialect so dashboards query the same analytics surface as the API.
"""

import os

# SQLAlchemy connection to the DuckDB warehouse.
# The database file lives on a shared volume mounted at /app/warehouse.
from pathlib import Path

WAREHOUSE_PATH = Path("/app/warehouse/air_traffic.duckdb")

DATABASE_DASHBOARD_POSITION_DATA = None
SUPERSET_FEATURE_EMBEDDED_SUPERSET = True
ENABLE_PROXY_FIX = True
SQLALCHEMY_DATABASE_URI = f"duckdb:///{WAREHOUSE_PATH}"
SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "please-change-me-in-production")

# Tunables
ROW_LIMIT = 5000
MAX_ROW = 100000
SQL_MAX_ROW = 100000
