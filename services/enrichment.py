"""Enrich a live position row in one place.

Attaches the derived fields every consumer needs — carbon estimate
(:mod:`services.emissions`) and operational classification
(:mod:`services.classification`) — so Kafka/Bronze, the live store and the
dashboard all see the same values.
"""

from __future__ import annotations

from typing import Any

from services.classification import classify
from services.emissions import enrich_emissions
from services.route_estimate import estimate_leg


def enrich_position(row: dict[str, Any]) -> dict[str, Any]:
    """Add emissions + classification fields to a canonical position (in place)."""
    enrich_emissions(row)
    row.update(
        classify(
            callsign=row.get("callsign"),
            aircraft_type=row.get("aircraft_type"),
            category=row.get("category"),
            registration=row.get("registration"),
            icao24=row.get("icao24"),
        )
    )
    # Geometric leg estimate only (origin *or* destination at most). The
    # collector upgrades this with the learned callsign memory; a pre-existing
    # observed/typical route is never downgraded.
    if row.get("route_source") not in ("observed", "typical"):
        leg = estimate_leg(row)
        row["route_origin"] = leg["origin"]
        row["route_destination"] = leg["destination"]
        if leg["origin"] and leg["destination"]:
            row["route"] = f"{leg['origin']} → {leg['destination']}"
            row["route_source"] = "observed"
        elif leg["origin"] or leg["destination"]:
            row["route"] = f"{leg['origin']} → ?" if leg["origin"] else f"? → {leg['destination']}"
            row["route_source"] = "estimated"
        else:
            row["route"] = None
            row["route_source"] = "none"
    return row


def summarise_airspace(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a position list into the class mix + headline live numbers."""
    by_class: dict[str, int] = {}
    by_emitter: dict[str, int] = {}
    by_source: dict[str, int] = {}
    total_co2 = measured_co2 = 0.0
    for row in positions:
        cls = str(row.get("aircraft_class") or "other")
        emitter = str(row.get("emitter_class") or "unknown")
        by_class[cls] = by_class.get(cls, 0) + 1
        by_emitter[emitter] = by_emitter.get(emitter, 0) + 1
        src = str(row.get("source") or "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        rate = float(row.get("co2_kg_per_hour") or 0.0)
        total_co2 += rate
        if not row.get("co2_estimated"):
            measured_co2 += rate
    return {
        "total": len(positions),
        "by_class": dict(sorted(by_class.items(), key=lambda kv: kv[1], reverse=True)),
        "by_emitter": dict(sorted(by_emitter.items(), key=lambda kv: kv[1], reverse=True)),
        "by_source": by_source,
        "military": by_class.get("military", 0),
        "cargo": by_class.get("cargo", 0),
        "helicopter": by_class.get("helicopter", 0),
        "passenger": by_class.get("passenger", 0),
        "private": by_class.get("private", 0),
        "total_co2_kg_per_hour": round(total_co2, 1),
        "measured_co2_kg_per_hour": round(measured_co2, 1),
    }
