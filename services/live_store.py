"""Thread-safe in-memory store of the latest live data.

The VPS collector writes every upstream poll here; the live API reads it. This
is the single source of truth for the dashboard's live view — no browser ever
touches a third-party API directly.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from services.enrichment import enrich_position, summarise_airspace

# Sections keyed by their logical source name.
_POSITION_SECTION = "positions"
_REFERENCE_KINDS = ("airport", "route", "aircraft", "emission", "holiday")


def _now() -> datetime:
    return datetime.now(UTC)


def _age_seconds(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return round((_now() - parsed).total_seconds(), 1)


class LiveStore:
    """Latest record per natural key, grouped by source section."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sections: dict[str, dict[str, dict[str, Any]]] = {}
        self._reference: dict[str, dict[str, dict[str, Any]]] = {}
        self._updated: dict[str, datetime] = {}
        self._totals: dict[str, int] = {}

    # ── writes ─────────────────────────────────────────────────────────────
    def update(self, section: str, records: list[dict[str, Any]], key: str) -> int:
        """Upsert records into a section. Returns the number stored."""
        if not records:
            return 0
        with self._lock:
            store = self._sections.setdefault(section, {})
            for row in records:
                if section == _POSITION_SECTION:
                    enrich_position(row)
                record_key = str(
                    row.get(key)
                    or row.get("icao24")
                    or row.get("id")
                    or f"{row.get('station_icao')}_{row.get('timestamp')}"
                )
                store[record_key] = row
            self._updated[section] = _now()
            self._totals[section] = self._totals.get(section, 0) + len(records)
            return len(records)

    def update_reference(self, records: list[dict[str, Any]]) -> int:
        """Split reference records by ``_kind`` into named sub-sections."""
        if not records:
            return 0
        with self._lock:
            for row in records:
                kind = str(row.get("_kind") or "other")
                bucket = self._reference.setdefault(kind, {})
                record_key = str(
                    row.get("id")
                    or row.get("airport_icao")
                    or row.get("icao")
                    or f"{kind}_{len(bucket)}"
                )
                bucket[record_key] = row
            self._updated["reference"] = _now()
            self._totals["reference"] = self._totals.get("reference", 0) + len(records)
            return len(records)

    # ── reads ──────────────────────────────────────────────────────────────
    def section(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sections.get(name, {}).values())

    def _prune_positions(self, max_age_seconds: float) -> None:
        with self._lock:
            store = self._sections.get(_POSITION_SECTION)
            if not store:
                return
            stale = [
                key
                for key, row in store.items()
                if (_age_seconds(row.get("collected_at")) or 0) > max_age_seconds
            ]
            for key in stale:
                store.pop(key, None)

    def _emissions_summary(self) -> dict[str, Any]:
        with self._lock:
            positions = list(self._sections.get(_POSITION_SECTION, {}).values())
        by_type: dict[str, dict[str, Any]] = {}
        by_source: dict[str, int] = {}
        total = measured = estimated_total = 0.0
        measured_n = estimated_n = 0
        for row in positions:
            rate = row.get("co2_kg_per_hour") or 0.0
            total += rate
            src = str(row.get("source") or "unknown")
            by_source[src] = by_source.get(src, 0) + 1
            if row.get("co2_estimated"):
                estimated_total += rate
                estimated_n += 1
            else:
                measured += rate
                measured_n += 1
            aircraft_type = str(row.get("aircraft_type") or "UNKNOWN")
            entry = by_type.setdefault(
                aircraft_type,
                {"aircraft_type": aircraft_type, "aircraft": 0, "co2_kg_per_hour": 0.0},
            )
            entry["aircraft"] += 1
            entry["co2_kg_per_hour"] = round(entry["co2_kg_per_hour"] + rate, 1)
        ranked = sorted(by_type.values(), key=lambda e: e["co2_kg_per_hour"], reverse=True)
        return {
            "total_co2_kg_per_hour": round(total, 1),
            "total_co2_tonnes_per_hour": round(total / 1000, 3),
            "measured_co2_kg_per_hour": round(measured, 1),
            "estimated_co2_kg_per_hour": round(estimated_total, 1),
            "measured_aircraft": measured_n,
            "estimated_aircraft": estimated_n,
            "by_source": by_source,
            "by_type": ranked,
        }

    def snapshot(self, max_age_seconds: float = 120.0) -> dict[str, Any]:
        """Full live payload for the dashboard's single endpoint."""
        # Drop aircraft not seen within the freshness window so the map never
        # shows "ghost" contacts from many minutes ago.
        self._prune_positions(max_age_seconds)
        with self._lock:
            positions = list(self._sections.get(_POSITION_SECTION, {}).values())
            flights = list(self._sections.get("flights", {}).values())
            metar = list(self._sections.get("metar", {}).values())
            taf = list(self._sections.get("taf", {}).values())
            forecast = list(self._sections.get("forecast", {}).values())
            fuel = list(self._sections.get("fuel", {}).values())
            updated = {k: v.isoformat() for k, v in self._updated.items()}

        reference = {kind: list(bucket.values()) for kind, bucket in self._reference.items()}
        return {
            "generatedAt": _now().isoformat(),
            "counts": {
                "positions": len(positions),
                "flights": len(flights),
                "metar": len(metar),
                "taf": len(taf),
                "forecast": len(forecast),
                "fuel": len(fuel),
                "airports": len(reference.get("airport", [])),
                "routes": len(reference.get("route", [])),
                "aircraft": len(reference.get("aircraft", [])),
            },
            "updatedAt": updated,
            "positions": positions,
            "flights": flights,
            "weather": {"metar": metar, "taf": taf, "forecast": forecast},
            "fuel": fuel,
            "airspace": summarise_airspace(positions),
            "reference": {
                "airports": reference.get("airport", []),
                "routes": reference.get("route", []),
                "aircraft": reference.get("aircraft", []),
                "emission_factors": reference.get("emission", []),
                "holidays": reference.get("holiday", []),
            },
            "emissions": self._emissions_summary(),
        }

    def health(self, max_age_seconds: float = 120.0) -> dict[str, Any]:
        """Per-section freshness and total counters for the status endpoint."""
        with self._lock:
            sections = {name: len(store) for name, store in self._sections.items()}
            reference = {kind: len(bucket) for kind, bucket in self._reference.items()}
            updated = {k: v.isoformat() for k, v in self._updated.items()}
            totals = dict(self._totals)
        return {
            "status": "ok",
            "sections": sections,
            "reference": reference,
            "updatedAt": updated,
            "ageSeconds": {k: _age_seconds(v) for k, v in updated.items()},
            "maxAgeSeconds": max_age_seconds,
            "totals": totals,
        }
