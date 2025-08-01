"""Air Traffic Analytics Platform — Interactive Streamlit Dashboard.

Run with: streamlit run streamlit_app.py

Provides:
- KPI overview with real-time metrics
- Airport performance analysis
- Airline benchmarking
- Weather impact explorer
- Delay analysis
- Data quality report
- SQL query console
- Pipeline trigger and monitoring
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import settings

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Air Traffic Analytics",
    page_icon="airplane",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Helpers ──────────────────────────────────────────────────────────────────
@st.cache_resource
def get_connection():
    """Read-only DuckDB connection (cached across reruns)."""
    if settings.duckdb_path.exists():
        return duckdb.connect(str(settings.duckdb_path), read_only=True)

    # If no local DB, try pulling from Hugging Face Hub (for Streamlit Cloud)
    import os

    try:
        from huggingface_hub import hf_hub_download

        repo_id = os.environ.get("HF_REPO", "swadhinbiswas/air-traffic")
        db_path = hf_hub_download(
            repo_id=repo_id, repo_type="dataset", filename="air_traffic.duckdb"
        )
        return duckdb.connect(db_path, read_only=True)
    except Exception as e:  # noqa: BLE001
        import streamlit as st

        st.error(f"Failed to connect to Hugging Face Hub: {e}")
        return None


def query_df(sql: str) -> pl.DataFrame | None:
    """Execute SQL and return a Polars DataFrame."""
    con = get_connection()
    if con is None:
        return None
    try:
        result = con.execute(sql).fetch_df()
        return pl.from_pandas(result)
    except duckdb.Error:
        return None


def query_json(sql: str) -> list[dict]:
    """Execute SQL and return list of dicts."""
    con = get_connection()
    if con is None:
        return []
    try:
        return con.execute(sql).fetch_df().to_dict(orient="records")
    except duckdb.Error:
        return []


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("Air Traffic Analytics")
    st.caption("European Aviation Data Platform")

    warehouse_exists = get_connection() is not None
    if warehouse_exists:
        st.success("Warehouse connected")
    else:
        st.warning("No warehouse found")
        st.caption("Run the pipeline first to generate data.")

    st.divider()

    page = st.radio(
        "Navigation",
        [
            "Overview",
            "Airports",
            "Airlines",
            "Weather",
            "Delays",
            "Quality",
            "SQL Console",
            "Pipeline",
        ],
        index=0,
    )

    st.divider()
    st.caption("Medallion: Bronze > Silver > Gold")
    st.caption("DuckDB + Polars + dbt")


# ── Overview page ────────────────────────────────────────────────────────────
if page == "Overview":
    st.header("Flight Operations Overview")

    kpis = query_json(
        """
        SELECT
            (SELECT COUNT(*) FROM fact_flights) AS total_flights,
            (SELECT COUNT(*) FROM fact_flights WHERE status = 'cancelled') AS cancelled,
            (SELECT ROUND(AVG(delay_minutes), 1) FROM fact_flights WHERE status != 'cancelled') AS avg_delay,
            (SELECT COUNT(*) FROM dim_airport) AS airports,
            (SELECT COUNT(*) FROM dim_airline) AS airlines,
            (SELECT COUNT(*) FROM dim_date) AS days_covered
        """
    )

    if kpis:
        k = kpis[0]
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("Total Flights", f"{k['total_flights']:,}")
        col2.metric("Cancelled", f"{k['cancelled']:,}")
        col3.metric("Avg Delay (min)", k["avg_delay"])
        col4.metric("Airports", f"{k['airports']:,}")
        col5.metric("Airlines", k["airlines"])
        col6.metric("Days Covered", k["days_covered"])

        st.divider()

        # Flight status distribution
        col_left, col_right = st.columns(2)

        with col_left:
            st.subheader("Flight Status Breakdown")
            status_data = query_df(
                "SELECT status, COUNT(*) as count FROM fact_flights GROUP BY status"
            )
            if status_data is not None and not status_data.is_empty():
                fig = px.pie(
                    status_data.to_pandas(),
                    values="count",
                    names="status",
                    color_discrete_map={
                        "scheduled": "#38bdf8",
                        "delayed": "#fbbf24",
                        "cancelled": "#f87171",
                        "landed": "#34d399",
                    },
                    hole=0.4,
                )
                fig.update_layout(height=350, margin={"t": 20, "b": 20, "l": 20, "r": 20})
                st.plotly_chart(fig, use_container_width=True)

        with col_right:
            st.subheader("Delay Distribution")
            delay_data = query_df(
                """
                SELECT
                    CASE
                        WHEN delay_minutes <= 0 THEN 'On time'
                        WHEN delay_minutes <= 15 THEN 'Minor (0-15 min)'
                        WHEN delay_minutes <= 60 THEN 'Moderate (15-60 min)'
                        WHEN delay_minutes <= 180 THEN 'Severe (1-3 hours)'
                        ELSE 'Critical (>3 hours)'
                    END AS delay_bucket,
                    COUNT(*) as count
                FROM fact_flights
                WHERE status != 'cancelled'
                GROUP BY delay_bucket
                ORDER BY count DESC
                """
            )
            if delay_data is not None and not delay_data.is_empty():
                fig = px.bar(
                    delay_data.to_pandas(),
                    x="delay_bucket",
                    y="count",
                    color="delay_bucket",
                    color_discrete_map={
                        "On time": "#34d399",
                        "Minor (0-15 min)": "#fbbf24",
                        "Moderate (15-60 min)": "#fb923c",
                        "Severe (1-3 hours)": "#f87171",
                        "Critical (>3 hours)": "#dc2626",
                    },
                )
                fig.update_layout(
                    height=350, showlegend=False, xaxis_title="", yaxis_title="Flights"
                )
                st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data available. Run the pipeline to generate flight data.")

# ── Airports page ────────────────────────────────────────────────────────────
elif page == "Airports":
    st.header("Airport Performance")

    data = query_df(
        """
        SELECT airport_icao, total_flights, avg_delay_minutes, max_delay_minutes, on_time_rate
        FROM gold_airport_metrics
        ORDER BY total_flights DESC
        LIMIT 30
        """
    )

    if data is not None and not data.is_empty():
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Top Airports by Volume")
            fig = px.bar(
                data.head(15).to_pandas(),
                x="airport_icao",
                y="total_flights",
                color="on_time_rate",
                color_continuous_scale="RdYlGn",
                labels={"on_time_rate": "OTP %"},
            )
            fig.update_layout(height=450, xaxis_title="", yaxis_title="Flights")
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.subheader("Delay vs Volume")
            fig = px.scatter(
                data.to_pandas(),
                x="total_flights",
                y="avg_delay_minutes",
                size="max_delay_minutes",
                hover_name="airport_icao",
                color="on_time_rate",
                color_continuous_scale="RdYlGn",
                labels={"on_time_rate": "OTP %"},
            )
            fig.update_layout(
                height=450, xaxis_title="Total Flights", yaxis_title="Avg Delay (min)"
            )
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Airport Details")
        st.dataframe(
            data.to_pandas().style.format(
                {
                    "avg_delay_minutes": "{:.1f}",
                    "max_delay_minutes": "{:.0f}",
                    "on_time_rate": "{:.1%}",
                }
            ),
            use_container_width=True,
        )
    else:
        st.info("No airport data available.")

# ── Airlines page ────────────────────────────────────────────────────────────
elif page == "Airlines":
    st.header("Airline Benchmarking")

    data = query_df(
        """
        SELECT airline_icao, airline_name, total_flights, avg_delay_minutes, on_time_rate
        FROM gold_airline_rankings
        ORDER BY total_flights DESC
        """
    )

    if data is not None and not data.is_empty():
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("On-Time Performance by Airline")
            fig = px.bar(
                data.to_pandas(),
                x="airline_icao",
                y="on_time_rate",
                color="on_time_rate",
                color_continuous_scale="RdYlGn",
                labels={"on_time_rate": "OTP %"},
            )
            fig.update_layout(
                height=400, xaxis_title="", yaxis_title="On-Time Rate", yaxis_tickformat=".0%"
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.subheader("Average Delay by Airline")
            fig = px.bar(
                data.to_pandas(),
                x="airline_icao",
                y="avg_delay_minutes",
                color="avg_delay_minutes",
                color_continuous_scale="RdYlGn_r",
            )
            fig.update_layout(height=400, xaxis_title="", yaxis_title="Avg Delay (min)")
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Airline Rankings")
        st.dataframe(
            data.to_pandas().style.format(
                {
                    "avg_delay_minutes": "{:.1f}",
                    "on_time_rate": "{:.1%}",
                }
            ),
            use_container_width=True,
        )
    else:
        st.info("No airline data available.")

# ── Weather page ─────────────────────────────────────────────────────────────
elif page == "Weather":
    st.header("Weather Impact Analysis")

    data = query_df(
        """
        SELECT weather_condition, flight_count, avg_delay_minutes, avg_temperature_c, avg_wind_speed_ms
        FROM gold_weather_impact
        ORDER BY flight_count DESC
        """
    )

    if data is not None and not data.is_empty():
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Delay by Weather Condition")
            fig = px.bar(
                data.to_pandas(),
                x="weather_condition",
                y="avg_delay_minutes",
                color="flight_count",
                color_continuous_scale="Blues",
            )
            fig.update_layout(height=400, xaxis_title="", yaxis_title="Avg Delay (min)")
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.subheader("Temperature vs Delay")
            fig = px.scatter(
                data.to_pandas(),
                x="avg_temperature_c",
                y="avg_delay_minutes",
                size="flight_count",
                hover_name="weather_condition",
                color="avg_wind_speed_ms",
                color_continuous_scale="Viridis",
                labels={"avg_wind_speed_ms": "Wind (m/s)"},
            )
            fig.update_layout(
                height=400, xaxis_title="Avg Temperature (C)", yaxis_title="Avg Delay (min)"
            )
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Weather Conditions Detail")
        st.dataframe(
            data.to_pandas().style.format(
                {
                    "avg_delay_minutes": "{:.1f}",
                    "avg_temperature_c": "{:.1f}",
                    "avg_wind_speed_ms": "{:.1f}",
                }
            ),
            use_container_width=True,
        )
    else:
        st.info("No weather impact data available.")

# ── Delays page ──────────────────────────────────────────────────────────────
elif page == "Delays":
    st.header("Delay Analysis")

    # Status breakdown
    status_data = query_df(
        "SELECT status, flight_count, avg_delay_minutes, min_delay_minutes, max_delay_minutes FROM gold_delay_analysis"
    )

    # Seasonal trends
    seasonal_data = query_df(
        """
        SELECT flight_date, SUM(flight_count) as daily_flights, AVG(avg_delay_minutes) as avg_delay
        FROM gold_seasonal_trends
        GROUP BY flight_date
        ORDER BY flight_date
        """
    )

    if status_data is not None and not status_data.is_empty():
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Status Breakdown")
            fig = px.bar(
                status_data.to_pandas(),
                x="status",
                y="flight_count",
                color="status",
                color_discrete_map={
                    "scheduled": "#38bdf8",
                    "delayed": "#fbbf24",
                    "cancelled": "#f87171",
                    "landed": "#34d399",
                },
            )
            fig.update_layout(height=350, showlegend=False, xaxis_title="", yaxis_title="Flights")
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.subheader("Delay Statistics by Status")
            st.dataframe(
                status_data.to_pandas().style.format(
                    {
                        "avg_delay_minutes": "{:.1f}",
                        "min_delay_minutes": "{:.0f}",
                        "max_delay_minutes": "{:.0f}",
                    }
                ),
                use_container_width=True,
            )

    if seasonal_data is not None and not seasonal_data.is_empty():
        st.subheader("Daily Flight Volume and Delay Trend")
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=seasonal_data["flight_date"].to_list(),
                y=seasonal_data["daily_flights"].to_list(),
                name="Flights",
                yaxis="y",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=seasonal_data["flight_date"].to_list(),
                y=seasonal_data["avg_delay"].to_list(),
                name="Avg Delay (min)",
                yaxis="y2",
                line={"color": "#f87171", "width": 2},
            )
        )
        fig.update_layout(
            height=400,
            yaxis={"title": "Flights"},
            yaxis2={"title": "Avg Delay (min)", "overlaying": "y", "side": "right"},
            xaxis_title="",
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        )
        st.plotly_chart(fig, use_container_width=True)

# ── Quality page ─────────────────────────────────────────────────────────────
elif page == "Quality":
    st.header("Data Quality Report")

    # Try to load the quality report
    quality_path = settings.checkpoint_dir / "quality_report.json"
    if quality_path.exists():
        report = json.loads(quality_path.read_text(encoding="utf-8"))

        st.subheader("Summary")
        summary = report.get("summary", {})
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Bronze Rows", f"{summary.get('bronze_rows', 0):,}")
        col2.metric("Total Silver Rows", f"{summary.get('silver_rows', 0):,}")
        col3.metric("Quarantined", f"{summary.get('quarantined_rows', 0):,}")
        col4.metric("Overall Pass Rate", f"{summary.get('overall_pass_rate', 0):.1%}")

        st.divider()
        st.subheader("Per-Source Breakdown")

        sources = report.get("sources", {})
        if sources:
            rows = []
            for source, metrics in sources.items():
                rows.append(
                    {
                        "Source": source,
                        "Bronze": metrics.get("bronze_rows", 0),
                        "Silver": metrics.get("silver_rows", 0),
                        "Quarantined": metrics.get("quarantined_rows", 0),
                        "Pass Rate": metrics.get("pass_rate"),
                        "Freshness": metrics.get("freshness", "N/A"),
                    }
                )
            df = pd.DataFrame(rows)

            fig = px.bar(
                df,
                x="Source",
                y=["Silver", "Quarantined"],
                barmode="stack",
                color_discrete_map={"Silver": "#34d399", "Quarantined": "#f87171"},
            )
            fig.update_layout(height=350, yaxis_title="Rows")
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(
                df.style.format({"Pass Rate": lambda x: f"{x:.1%}" if x else "N/A"}),
                use_container_width=True,
            )

        st.caption(f"Generated at: {report.get('generated_at', 'N/A')}")
    else:
        st.info("No quality report found. Run the pipeline to generate one.")

    # Also show quarantine files
    quarantine_dir = settings.quarantine_dir
    if quarantine_dir.exists():
        q_files = list(quarantine_dir.rglob("*.parquet"))
        if q_files:
            st.divider()
            st.subheader(f"Quarantine Files ({len(q_files)} total)")
            for source_dir in quarantine_dir.iterdir():
                if source_dir.is_dir():
                    files = list(source_dir.glob("*.parquet"))
                    if files:
                        total_rows = sum(pl.read_parquet(f).height for f in files)
                        st.write(f"**{source_dir.name}**: {len(files)} files, {total_rows:,} rows")

# ── SQL Console page ─────────────────────────────────────────────────────────
elif page == "SQL Console":
    st.header("SQL Query Console")

    st.caption("Read-only queries against the DuckDB warehouse")

    default_query = """SELECT airline_icao, airline_name, total_flights, avg_delay_minutes, on_time_rate
FROM gold_airline_rankings
ORDER BY avg_delay_minutes
LIMIT 10"""

    sql = st.text_area("SQL Query", value=default_query, height=150)

    if st.button("Execute Query", type="primary"):
        with st.spinner("Executing..."):
            result = query_df(sql)
            if result is not None:
                st.success(f"Returned {result.height} rows")
                st.dataframe(result.to_pandas(), use_container_width=True)

                # Download button
                csv = result.to_pandas().to_csv(index=False)
                st.download_button("Download CSV", csv, "query_result.csv", "text/csv")
            else:
                st.error("Query failed. Check your SQL syntax.")

    # Quick query buttons
    st.divider()
    st.subheader("Quick Queries")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Top Airports"):
            st.session_state["quick_sql"] = (
                "SELECT * FROM gold_airport_metrics ORDER BY total_flights DESC LIMIT 10"
            )
    with col2:
        if st.button("Airline Rankings"):
            st.session_state["quick_sql"] = (
                "SELECT * FROM gold_airline_rankings ORDER BY avg_delay_minutes"
            )
    with col3:
        if st.button("Weather Impact"):
            st.session_state["quick_sql"] = (
                "SELECT * FROM gold_weather_impact ORDER BY flight_count DESC"
            )

    if "quick_sql" in st.session_state:
        st.code(st.session_state["quick_sql"], language="sql")

# ── Pipeline page ────────────────────────────────────────────────────────────
elif page == "Pipeline":
    st.header("Pipeline Management")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Run Pipeline")
        st.caption(
            "Execute the full ETL pipeline (collect, bronze, silver, gold, warehouse, quality)"
        )

        if st.button("Run Full Pipeline", type="primary"):
            with st.spinner("Running pipeline..."):
                from pipelines.orchestrator import Orchestrator

                report = Orchestrator().run()
                st.session_state["last_report"] = report

        if "last_report" in st.session_state:
            report = st.session_state["last_report"]
            if report.success:
                st.success(f"Pipeline completed in {report.elapsed_seconds:.1f}s")
            else:
                st.error(f"Pipeline failed: {report.errors}")

            st.subheader("Step Timings")
            steps = []
            for name, step in report.steps.items():
                steps.append(
                    {
                        "Step": name,
                        "Status": step.get("status", "unknown"),
                        "Duration (s)": step.get("elapsed_seconds", 0),
                    }
                )
            st.dataframe(pd.DataFrame(steps), use_container_width=True)

    with col2:
        st.subheader("Last Pipeline Report")
        report_path = settings.checkpoint_dir / "pipeline_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            st.json(report)
        else:
            st.info("No pipeline report found. Run the pipeline first.")

    st.divider()
    st.subheader("System Status")

    con = get_connection()
    if con:
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables ORDER BY table_name"
        ).fetchall()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Tables", len(tables))
        with col2:
            fact_count = con.execute("SELECT COUNT(*) FROM fact_flights").fetchone()[0]
            st.metric("Fact Rows", f"{fact_count:,}")
        with col3:
            total_tables = con.execute(
                "SELECT SUM(row_count) FROM (SELECT table_name, (SELECT COUNT(*) FROM information_schema.tables t2 WHERE t1.table_name = t2.table_name) as row_count FROM information_schema.tables t1)"
            ).fetchone()[0]
            st.metric("Total Rows", f"{total_tables:,}")

        st.subheader("Table Inventory")
        table_data = []
        for (table_name,) in tables:
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                table_data.append({"Table": table_name, "Rows": count})
            except duckdb.Error:
                table_data.append({"Table": table_name, "Rows": 0})
        st.dataframe(pd.DataFrame(table_data), use_container_width=True)
    else:
        st.warning("Warehouse not available. Run the pipeline first.")

# ── Footer ───────────────────────────────────────────────────────────────────
st.divider()
st.caption("Air Traffic Analytics Platform | Medallion Architecture | DuckDB + Polars + dbt")
