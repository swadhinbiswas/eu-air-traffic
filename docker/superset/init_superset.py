"""Bootstrap Superset for the Air Traffic Platform.

Run once by the ``superset-init`` container after ``superset init``.
Creates the DuckDB database connection and programmatically builds a
dashboard with charts that query the Gold layer (no fragile JSON imports).

Charts created:
- Big Number: total flights
- Bar Chart: top airports by volume
- Bar Chart: airline ranking by avg delay
- Donut Chart: flight status breakdown

All charts are driven by the same ``fact_flights`` dataset exposed through the
DuckDB connection, so they render as soon as the warehouse exists.
"""

from __future__ import annotations

from pathlib import Path

from superset import db
from superset.models.core import Database
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice

WAREHOUSE_PATH = Path("/app/warehouse/air_traffic.duckdb")
WAREHOUSE_DB_NAME = "air_traffic_duckdb"


# ── Database connection ───────────────────────────────────────────────────────
def ensure_duckdb_database() -> Database:
    uri = f"duckdb:///{WAREHOUSE_PATH}"
    existing = db.session.query(Database).filter(Database.sqlalchemy_uri == uri).first()
    if existing:
        return existing
    database = Database(
        database_name=WAREHOUSE_DB_NAME,
        sqlalchemy_uri=uri,
        allow_dml=False,
        expose_in_sqllab=True,
    )
    db.session.add(database)
    db.session.commit()
    print(f"Created Superset database connection: {database.database_name}")
    return database


def _find_dataset(database: Database, name: str, sql: str):
    from superset.models.dataset import Dataset

    existing = (
        db.session.query(Dataset)
        .filter(Dataset.table_name == name, Dataset.database == database)
        .first()
    )
    if existing:
        return existing
    dataset = Dataset(
        database=database,
        table_name=name,
        sql=sql,
        columns=[],
        metrics=[],
        owners=[],
        is_sqllab_view=True,
    )
    db.session.add(dataset)
    db.session.commit()
    print(f"Created dataset: {name}")
    return dataset


def _find_slice(name: str, dataset, viz_type: str, params: dict, title: str) -> Slice:
    existing = db.session.query(Slice).filter(Slice.slice_name == name).first()
    if existing:
        existing.viz_type = viz_type
        existing.params = params
        existing.datasource = dataset
        db.session.commit()
        print(f"Updated slice: {name}")
        return existing
    from superset.datasource.sqla import SqlaTable

    slice_obj = Slice(
        slice_name=name,
        viz_type=viz_type,
        datasource_type="table",
        datasource_id=dataset.id,
        params=params,
        datasource=dataset,
        owners=[],
        table=SqlaTable,
        query_context=None,
    )
    db.session.add(slice_obj)
    db.session.commit()
    print(f"Created slice: {name}")
    return slice_obj


def build_dashboard(database: Database) -> None:
    """Create dataset, charts and dashboard if they do not yet exist."""
    flights = _find_dataset(
        database,
        "fact_flights_gold",
        "SELECT * FROM fact_flights",
    )

    big_number = _find_slice(
        "Total Flights",
        flights,
        "big_number_total",
        {"metric": "count", "subheader": "Landed flights", "time_grain_sqla": "P1D"},
        "Total Flights",
    )
    airport_bar = _find_slice(
        "Top Airports by Volume",
        flights,
        "dist_bar",
        {
            "metrics": ["count"],
            "groupby": ["departure_icao"],
            "viz_type": "dist_bar",
        },
        "Top Airports by Volume",
    )
    airline_bar = _find_slice(
        "Airline Avg Delay (min)",
        flights,
        "dist_bar",
        {
            "metrics": [{"aggregate": "AVG", "column": {"column_name": "delay_minutes"}}],
            "groupby": ["airline_icao"],
            "viz_type": "dist_bar",
        },
        "Airline Avg Delay (min)",
    )
    status_pie = _find_slice(
        "Flight Status Breakdown",
        flights,
        "pie",
        {
            "metric": "count",
            "groupby": ["status"],
            "viz_type": "pie",
        },
        "Flight Status Breakdown",
    )

    dashboard = (
        db.session.query(Dashboard)
        .filter(Dashboard.dashboard_title == "Air Traffic Overview")
        .first()
    )
    if not dashboard:
        dashboard = Dashboard(
            dashboard_title="Air Traffic Overview",
            slug="air-traffic-overview",
            owners=[],
            published=True,
        )
        db.session.add(dashboard)
        db.session.flush()
        print("Created dashboard: Air Traffic Overview")

    # Position the charts in a 2x2 grid (Superset position data format).
    position_data = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {
            "type": "GRID",
            "id": "GRID_ID",
            "children": ["ROW-1", "ROW-2"],
            "parents": ["ROOT_ID"],
        },
        "ROW-1": {
            "type": "ROW",
            "id": "ROW-1",
            "children": ["BIG", "STATUS"],
            "parents": ["ROOT_ID", "GRID_ID"],
        },
        "ROW-2": {
            "type": "ROW",
            "id": "ROW-2",
            "children": ["AIRPORT", "AIRLINE"],
            "parents": ["ROOT_ID", "GRID_ID"],
        },
    }
    slices = [
        ("BIG", big_number, 0, 0),
        ("STATUS", status_pie, 6, 0),
        ("AIRPORT", airport_bar, 0, 6),
        ("AIRLINE", airline_bar, 6, 6),
    ]
    for key, slice_obj, row, col in slices:
        position_data[key] = {
            "type": "CHART",
            "id": key,
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID", "ROW-1" if row == 0 else "ROW-2"],
            "meta": {
                "chartId": slice_obj.id,
                "width": 6,
                "height": 6,
                "sliceName": slice_obj.slice_name,
            },
        }
        if slice_obj not in dashboard.slices:
            dashboard.slices.append(slice_obj)

    dashboard.position_json = position_data
    db.session.commit()
    print(f"Dashboard ready with {len(dashboard.slices)} chart(s).")


def main() -> None:
    database = ensure_duckdb_database()
    build_dashboard(database)
    print("Superset bootstrap complete.")


if __name__ == "__main__":
    main()
