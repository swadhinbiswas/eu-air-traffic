"""Fetch Eurostat monthly airport traffic (official benchmark layer).

`avia_paoa` reports passenger numbers for ~870 European airports, monthly,
~2 months behind. It is a *benchmark*, not live data: the Gold layer joins it
to our counted movements so the airport leaderboard can be cross-checked
against the official figures.

One request covers every month in the dataset, so this runs at most monthly:

    python -m scripts.fetch_eurostat
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import requests

from config.logging import logger
from config.settings import settings

EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/avia_paoa"
# Monthly boarded passengers, all schedule types and coverage.
FILTERS = {
    "format": "JSON",
    "lang": "EN",
    "freq": "M",
    "unit": "PAS",
    "tra_meas": "PAS_BRD",
    "schedule": "TOTAL",
}


def _decode(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Decode JSON-stat 2.0 into rows filtered to the requested dimensions."""
    order: list[str] = payload.get("id") or []
    size: list[int] = payload.get("size") or []
    if not order or not size:
        return []
    dimensions = payload.get("dimension") or {}
    categories: dict[str, list[str]] = {}
    for name in order:
        index = (dimensions.get(name) or {}).get("category", {}).get("index") or {}
        if isinstance(index, dict):
            categories[name] = [code for code, _ in sorted(index.items(), key=lambda kv: kv[1])]
        else:
            categories[name] = [str(entry) for entry in index]

    airport_at = order.index("rep_airp")
    time_at = order.index("time")
    rows: list[dict[str, Any]] = []
    for flat, value in (payload.get("value") or {}).items():
        if value is None:
            continue
        remaining = int(flat)
        indices = [0] * len(order)
        for position in range(len(order) - 1, -1, -1):
            indices[position] = remaining % size[position]
            remaining //= size[position]
        airport = categories["rep_airp"][indices[airport_at]]
        period = categories["time"][indices[time_at]]
        # Eurostat airport codes look like "DE_EDDF"; the ICAO part joins dim_airport.
        icao = airport.split("_")[-1].upper()
        if len(icao) != 4 or not icao.isalnum():
            continue
        try:
            passengers = int(float(value))
        except (TypeError, ValueError):
            continue
        rows.append({"period": str(period), "airport_icao": icao, "passengers": passengers})
    return rows


def _recent_months(count: int) -> list[str]:
    """The last ``count`` calendar months as YYYY-MM (Eurostat rejects bulk pulls)."""
    today = datetime.now(UTC).date()
    months: list[str] = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


def fetch(months: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period in _recent_months(months):
        response = requests.get(EUROSTAT_URL, params={**FILTERS, "time": period}, timeout=60)
        response.raise_for_status()
        rows.extend(_decode(response.json()))
    return rows


def write_parquet(app_settings=None, months: int = 12) -> Path:
    cfg = app_settings or settings
    rows = fetch(months)
    if not rows:
        raise RuntimeError("[eurostat] no observations returned — refusing to write an empty file")
    fetched_at = datetime.now(UTC).isoformat()
    frame = (
        pl.DataFrame(rows)
        .with_columns(
            pl.lit("eurostat").alias("source"),
            pl.lit(fetched_at).alias("fetched_at"),
        )
        .unique(subset=["period", "airport_icao"], keep="last")
        .sort(["period", "airport_icao"])
    )
    target = Path(cfg.project_root) / "services" / "data" / "eurostat_airport_traffic.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(target, compression="zstd")
    logger.info(
        "[eurostat] %s rows · %s airports · %s..%s → %s",
        frame.height,
        frame["airport_icao"].n_unique(),
        frame["period"].min(),
        frame["period"].max(),
        target,
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Eurostat airport traffic")
    parser.parse_args()
    target = write_parquet()
    print(json.dumps({"file": str(target)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
