"""Generate and publish the Hugging Face dataset card for the data lake.

The Hub repo holds timestamped Bronze windows plus the 11 Silver snapshots.
This module renders a full ``README.md`` card (YAML front matter with
``configs``/features/tags/license plus a prose body) from a single
``DATASET`` structure, so the card and the docs/tests can all read from the
same source of truth:

    python -m scripts.hf_dataset_card          # print the card
    python -m scripts.hf_dataset_card --push   # upload README.md to the Hub

Only ``README.md`` at the repo root is ever written. A legacy ``README.yaml``
sidecar is never created.
"""

from __future__ import annotations

import json
from typing import Any

from config.logging import logger
from config.settings import settings


def get_repo_id(app_settings: Any = None) -> str:
    """The Hub dataset repo id, from configuration."""
    s = app_settings or settings
    return str(s.huggingface_repo or "").strip()


def get_card_repo_path() -> str:
    """The only file this module may write on the Hub."""
    return "README.md"


REPO_ID = "swadhinbiswas/air-traffic"
REPO_URL = "https://huggingface.co/datasets/swadhinbiswas/air-traffic"
GITHUB_URL = "https://github.com/swadhinbiswas/eu-air-traffic"

COLUMN_LIMITATION_NOTE_BY_SPLIT = {
    "positions": "Type-specific columns may be null (e.g. squawk only for transponders that send it).",
    "flights": "delay_minutes/actual_* are null when the source has no schedule (OpenSky movements).",
    "weather": "wind_dir_deg is stored as a string.",
}

# Fixed presentation order.
SILVER_ORDER = [
    "positions",
    "flights",
    "weather",
    "weather_forecast",
    "weather_taf",
    "fuel",
    "routes",
    "airports",
    "holidays",
    "aircraft",
    "notams",
    "emissions",
]

# (file in repo, short description). Verified against the live Hub listing.
SILVER_FILES = {
    "positions": (
        "silver/positions/data.parquet",
        "Live aircraft position snapshots (deduped by airframe).",
    ),
    "flights": (
        "silver/flights/data.parquet",
        "Completed movements from OpenSky plus AirLabs schedules.",
    ),
    "weather": ("silver/weather/data.parquet", "METAR observations with flight categories."),
    "weather_forecast": ("silver/weather_forecast/data.parquet", "Open-Meteo hourly forecasts."),
    "weather_taf": ("silver/weather_taf/data.parquet", "Raw terminal aerodrome forecasts."),
    "fuel": ("silver/fuel/data.parquet", "Daily jet-fuel prices by region."),
    "routes": (
        "silver/routes/data.parquet",
        "EU route network, ICAO-mapped with great-circle distance.",
    ),
    "airports": ("silver/airports/airports.parquet", "Enriched EU airport reference."),
    "holidays": ("silver/holidays/data.parquet", "Public-holiday calendar by country."),
    "aircraft": ("silver/aircraft/data.parquet", "Aircraft type reference (ICAO 8643 + specs)."),
    "notams": ("silver/notams/data.parquet", "Notices to air missions."),
    "emissions": ("silver/emissions/data.parquet", "Per-type hourly fuel and CO2 rates."),
}

# Key columns only — the full list per dataset follows in the field reference.
KEY_COLUMNS = {
    "positions": ["icao24", "callsign", "latitude", "longitude", "altitude", "velocity"],
    "flights": [
        "flight_id",
        "callsign",
        "departure_icao",
        "arrival_icao",
        "status",
        "delay_minutes",
    ],
    "weather": ["station_icao", "timestamp", "temperature_c", "wind_speed_kt", "flight_category"],
    "weather_forecast": ["station_icao", "timestamp", "temperature_c", "condition"],
    "weather_taf": ["station_icao", "issue_time", "valid_from", "valid_to", "raw_taf"],
    "fuel": ["date", "region", "price_per_litre", "currency"],
    "routes": ["airline", "origin", "destination", "distance_km", "stops", "equipment"],
    "airports": ["ident", "name", "type", "latitude_deg", "longitude_deg", "iata_code"],
    "holidays": ["country", "date", "name"],
    "aircraft": ["type_icao", "manufacturer", "family", "capacity", "range_km"],
    "notams": ["notam_id", "icao_location", "notam_type", "valid_from", "valid_to"],
    "emissions": ["aircraft_type", "fuel_burn_kg_per_hour", "co2_kg_per_hour"],
}

# Exact name/dtype pairs, read from the committed Silver Parquet files.
FEATURES = {
    "positions": [
        ("icao24", "string"),
        ("callsign", "string"),
        ("registration", "string"),
        ("aircraft_type", "string"),
        ("latitude", "float64"),
        ("longitude", "float64"),
        ("altitude", "float64"),
        ("altitude_geom", "float64"),
        ("velocity", "float64"),
        ("heading", "float64"),
        ("vertical_rate", "float64"),
        ("mach", "float64"),
        ("ias", "float64"),
        ("tas", "float64"),
        ("oat", "float64"),
        ("wind_dir", "float64"),
        ("wind_speed", "float64"),
        ("squawk", "string"),
        ("emergency", "string"),
        ("category", "string"),
        ("on_ground", "bool"),
        ("source", "string"),
        ("collected_at", "string"),
        ("fuel_burn_kg_per_hour", "float64"),
        ("co2_kg_per_hour", "float64"),
        ("aircraft_class", "string"),
        ("emitter_class", "string"),
        ("is_cargo", "bool"),
        ("is_military", "bool"),
        ("operator_name", "string"),
        ("operator_country", "string"),
        ("operator_category", "string"),
        ("type_name", "string"),
        ("manufacturer", "string"),
        ("airframe", "string"),
        ("wake_category", "string"),
    ],
    "flights": [
        ("flight_id", "string"),
        ("callsign", "string"),
        ("airline_icao", "string"),
        ("airline_name", "string"),
        ("departure_icao", "string"),
        ("departure_iata", "string"),
        ("arrival_icao", "string"),
        ("arrival_iata", "string"),
        ("scheduled_departure", "timestamp[ns]"),
        ("scheduled_arrival", "timestamp[ns]"),
        ("actual_departure", "timestamp[ns]"),
        ("actual_arrival", "timestamp[ns]"),
        ("status", "string"),
        ("delay_minutes", "float64"),
        ("cancelled", "bool"),
        ("source", "string"),
        ("collected_at", "string"),
        ("ingestion_date", "string"),
    ],
    "weather": [
        ("station_icao", "string"),
        ("timestamp", "timestamp[ns]"),
        ("temperature_c", "float64"),
        ("humidity_pct", "float64"),
        ("wind_speed_ms", "float64"),
        ("visibility_m", "float64"),
        ("condition", "string"),
        ("pressure_hpa", "float64"),
        ("source", "string"),
        ("ingestion_date", "string"),
        ("name", "string"),
        ("latitude", "float64"),
        ("longitude", "float64"),
        ("dewpoint_c", "float64"),
        ("wind_dir_deg", "string"),
        ("wind_speed_kt", "float64"),
        ("gust_kt", "int64"),
        ("visibility", "string"),
        ("altimeter_hpa", "int64"),
        ("flight_category", "string"),
        ("cover", "string"),
        ("raw_metar", "string"),
        ("observed_at", "string"),
        ("collected_at", "string"),
        ("visibility_raw", "string"),
    ],
    "weather_forecast": [
        ("station_icao", "string"),
        ("timestamp", "timestamp[ns]"),
        ("is_forecast", "bool"),
        ("temperature_c", "float64"),
        ("humidity_pct", "float64"),
        ("precipitation_mm", "float64"),
        ("wind_speed_ms", "float64"),
        ("wind_direction_deg", "float64"),
        ("weather_code", "int64"),
        ("condition", "string"),
        ("wind_unit", "string"),
        ("source", "string"),
        ("collected_at", "string"),
        ("ingestion_date", "string"),
    ],
    "weather_taf": [
        ("station_icao", "string"),
        ("issue_time", "string"),
        ("valid_from", "int64"),
        ("valid_to", "int64"),
        ("raw_taf", "string"),
        ("source", "string"),
        ("collected_at", "string"),
    ],
    "fuel": [
        ("date", "string"),
        ("region", "string"),
        ("price_per_litre", "float64"),
        ("currency", "string"),
        ("source", "string"),
        ("collected_at", "string"),
        ("ingestion_date", "string"),
        ("series_key", "string"),
    ],
    "routes": [
        ("airline", "string"),
        ("origin", "string"),
        ("destination", "string"),
        ("stops", "int64"),
        ("equipment", "string"),
        ("distance_km", "float64"),
        ("source", "string"),
        ("collected_at", "string"),
        ("ingestion_date", "string"),
        ("_kind", "string"),
        ("id", "string"),
    ],
    "airports": [
        ("ident", "string"),
        ("name", "string"),
        ("type", "string"),
        ("latitude_deg", "float64"),
        ("longitude_deg", "float64"),
        ("elevation_ft", "string"),
        ("iso_country", "string"),
        ("municipality", "string"),
        ("iata_code", "string"),
        ("score", "string"),
        ("scheduled_service", "string"),
        ("ingestion_date", "string"),
    ],
    "holidays": [
        ("country", "string"),
        ("date", "string"),
        ("name", "string"),
        ("source", "string"),
        ("collected_at", "string"),
        ("ingestion_date", "string"),
        ("_kind", "string"),
        ("id", "string"),
    ],
    "aircraft": [
        ("type_iata", "string"),
        ("type_icao", "string"),
        ("manufacturer", "string"),
        ("family", "string"),
        ("engine", "string"),
        ("capacity", "int64"),
        ("range_km", "int64"),
        ("source", "string"),
        ("collected_at", "string"),
        ("ingestion_date", "string"),
        ("_kind", "string"),
        ("id", "string"),
    ],
    "notams": [
        ("notam_id", "string"),
        ("icao_location", "string"),
        ("notam_type", "string"),
        ("message", "string"),
        ("qualification", "string"),
        ("valid_from", "string"),
        ("valid_to", "string"),
        ("source", "string"),
        ("collected_at", "string"),
        ("ingestion_date", "string"),
    ],
    "emissions": [
        ("aircraft_type", "string"),
        ("fuel_burn_liters_per_hour", "int64"),
        ("fuel_burn_kg_per_hour", "float64"),
        ("co2_kg_per_hour", "float64"),
        ("co2_tonnes_per_hour", "float64"),
        ("emission_factor_kg_per_kg_fuel", "float64"),
        ("fuel_density_kg_per_liter", "float64"),
        ("source", "string"),
        ("collected_at", "string"),
        ("ingestion_date", "string"),
        ("_kind", "string"),
        ("id", "string"),
    ],
}

GOLD_MARTS = [
    "airport_metrics",
    "airline_rankings",
    "delay_analysis",
    "weather_impact",
    "seasonal_trends",
    "fuel_price_series",
    "aircraft_class_mix",
]


def _front_matter() -> str:
    lines = [
        "---",
        "language:",
        "  - en",
        "license: mit",
        "tags:",
        "  - aviation",
        "  - ads-b",
        "  - flight-tracking",
        "  - meteorology",
        "  - meteorology-aviation",
        "  - open-data",
        "  - parquet",
        "  - time-series",
        "task_categories:",
        "  - tabular-classification",
        "  - tabular-regression",
        "  - time-series-forecasting",
        "pretty_name: EU Air Traffic Lake",
        "configs:",
        "- config_name: silver",
        "  data_files:",
    ]
    for split in SILVER_ORDER:
        path, _ = SILVER_FILES[split]
        lines.append(f"  - split: {split}")
        lines.append(f'    path: "{path}"')
    lines.append("- config_name: bronze")
    lines.append("  data_files:")
    lines.append("  - split: parquet")
    lines.append('    path: "bronze/parquet/*/*.parquet"')
    lines.append("  - split: raw")
    lines.append('    path: "bronze/raw/*/*/*.jsonl"')
    return "\n".join(lines) + "\n---\n"


def _field_line(split: str) -> str:
    names = ", ".join(f"`{name}`" for name, _ in FEATURES[split])
    return names


def _body() -> str:
    parts = [
        "# EU Air Traffic Lake",
        "",
        "Live and scheduled European air traffic as versioned Parquet: raw Bronze intake windows from the collector, plus curated Silver snapshots refreshed by a scheduled pipeline.",
        "",
        "## Updates",
        "",
        "- Bronze windows land continuously (positions every ~5 min, weather every 5 min, departures twice an hour, arrivals backfilled nightly).",
        "- Each of the Silver snapshots below is replaced every 15 minutes by the lake pipeline.",
        "- The Eurostat airport-traffic benchmark and dbt Gold marts are not stored here; see [eu-air-traffic](%s) for the warehouse."
        % GITHUB_URL,
        "",
        "## Contents",
        "",
        "### Silver snapshots (`silver/<source>/data.parquet`)",
        "",
    ]
    for split in SILVER_ORDER:
        path, blurb = SILVER_FILES[split]
        parts.append(f"- `{path}` — {blurb}")
        keys = ", ".join(f"`{c}`" for c in KEY_COLUMNS[split])
        parts.append(f"  Key columns: {keys}.")
    parts += [
        "",
        "### Bronze intake windows",
        "",
        "- `bronze/parquet/<source>/<source>_YYYY-MM-DDTHHMMSSffffffZ.parquet` — immutable windows exactly as drained from Kafka.",
        "- `bronze/raw/<source>/<date>/*.jsonl` — the same records before Parquet conversion.",
        "- Windows accumulate: download a prefix such as `bronze/parquet/flights/` for history.",
        "",
        "## Field reference",
        "",
    ]
    for split in SILVER_ORDER:
        path, _ = SILVER_FILES[split]
        parts.append(f"<details><summary><code>{path}</code></summary>")
        parts.append("")
        parts.append(_field_line(split))
        note = COLUMN_LIMITATION_NOTE_BY_SPLIT.get(split)
        if note:
            parts.append("")
            parts.append(f"Note: {note}")
        parts.append("")
        parts.append("</details>")
        parts.append("")
    parts += [
        "## Data sources",
        "",
        "- Positions: ADS-B via adsb.lol, with airplanes.live and OpenSky fallbacks.",
        "- Movements and delays: OpenSky (`/flights/*`) plus AirLabs schedules.",
        "- Weather: METAR/TAF from aviationweather.gov, forecasts from Open-Meteo.",
        "- Reference: airports, routes, aircraft, emissions and holidays built from bundled reference data.",
        "- Official passengers: Eurostat `avia_paoa` (monthly, ~2 months behind), loaded as a benchmark layer.",
        "",
        "## Reproducibility",
        "",
        "- Bronze files are immutable and timestamped; Silver files are snapshots of the latest state.",
        "- Local paths mirror the repo: `warehouse/bronze/<source>/…`, `warehouse/silver/<source>/…`.",
        "- Regenerate locally with `uv run python -m scripts.lake_sync pull-silver`, then `uv run pytest`; see `docs/huggingface-dataset.md` for the scripts and cadence.",
        "",
        "## License",
        "",
        "MIT — same license as the [eu-air-traffic](%s) repository." % GITHUB_URL,
        "",
        "## Citation",
        "",
        "```bibtex",
        "@misc{swadhinbiswas_air_traffic_lake,",
        "  author = {Swadhin Biswas},",
        "  title = {EU Air Traffic Lake},",
        "  year = {2026},",
        "  publisher = {Hugging Face},",
        "  url = {" + REPO_URL + "}",
        "}",
        "```",
        "",
        "## Limitations",
        "",
        "- OpenSky movements carry no schedule, so delay fields are null there; use AirLabs schedules or Gold delay marts for punctuality.",
        "- Arrivals are backfilled nightly; same-day arrival coverage lags.",
        "- METAR wind direction is a string in the source feed (`wind_dir_deg`), not numeric.",
        "- TAF validity bounds are epoch integers, not ISO timestamps.",
        "- This dataset mirrors the live pipeline: expect drifting schemas over months, pinned by Silver snapshots.",
        "",
    ]
    return "\n".join(parts)


def render_card(app_settings: Any = None) -> str:
    """Render the full dataset card text (front matter + body)."""
    _ = app_settings
    return _front_matter() + "\n" + _body()


def push_card(app_settings: Any = None) -> dict[str, Any]:
    """Upload the rendered card as ``README.md``. Never touches other files."""
    from config.settings import settings as global_settings

    s = app_settings or global_settings
    repo = str(getattr(s, "huggingface_repo", "") or "").strip()
    token = getattr(s, "huggingface_token", None)
    if not token:
        logger.warning("[hf-card] HF_TOKEN not set — skipping card push")
        return {"skipped": True, "reason": "HF_TOKEN not set"}
    if repo != REPO_ID:
        raise ValueError(f"[hf-card] refusing to publish: repo is {repo!r}, expected {REPO_ID!r}")
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover - optional dependency
        logger.warning("[hf-card] huggingface_hub not installed — skipping")
        return {"skipped": True, "reason": str(exc)}
    api = HfApi(token=token)
    info = api.repo_info(repo_id=repo, repo_type="dataset")
    if getattr(info, "private", False):
        raise ValueError(f"[hf-card] refusing to publish: {repo} is private, must be public")
    api.upload_file(
        path_or_fileobj=__import__("io").BytesIO(render_card(s).encode("utf-8")),
        path_in_repo=get_card_repo_path(),
        repo_id=repo,
        repo_type="dataset",
        commit_message="docs: dataset card (generated, do not hand-edit)",
    )
    logger.info("[hf-card] published card to %s/%s", repo, get_card_repo_path())
    return {"repo": repo, "path": get_card_repo_path(), "published": True}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Dataset card for the HF lake")
    parser.add_argument("--push", action="store_true", help="upload README.md to the Hub")
    args = parser.parse_args(argv)
    if args.push:
        print(json.dumps(push_card(), indent=2, default=str))
    else:
        print(render_card())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
