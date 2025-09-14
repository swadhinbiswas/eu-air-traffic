"""Live schedules from AirLabs — the only feed with planned times.

OpenSky movements say a flight happened; they cannot say whether it was late.
AirLabs schedules carry ``dep_time``/``arr_time`` plus ``dep_delayed`` /
``arr_delayed``, which is what the delay and punctuality marts need.

Budget discipline (free key): **1,000 calls/month, max 50 rows per call**, and
schedules only look ~10 hours ahead. So this source:

* fetches a rotating pair of hubs per cycle (default every 6 h → ~360 calls/mo),
* keeps a persisted ``calls`` counter per calendar month and stops when the
  budget is reached, and
* resolves IATA→ICAO from the bundled OurAirports list — no extra API call.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from config.logging import logger
from services.sources.base import Source
from services.sources.flights import TOP_AIRPORTS

AIRLABS = "https://airlabs.co/api/v9/schedules"

HEADERS = {
    "User-Agent": "eu-air-traffic-collector/2.0 (+https://github.com/swadhinbiswas/air-traffic)",
    "Accept": "application/json",
}

_FIELDS = (
    "flight_iata,flight_icao,airline_iata,dep_iata,dep_icao,dep_time,dep_time_utc,"
    "dep_actual_utc,arr_iata,arr_icao,arr_time,arr_time_utc,arr_actual_utc,"
    "status,delayed,dep_delayed,arr_delayed"
)

_STATUS = {
    "landed": "landed",
    "cancelled": "cancelled",
    "diverted": "diverted",
    "active": "en-route",
    "scheduled": "scheduled",
}


def _iata_to_icao() -> dict[str, str]:
    """Map from the bundled OurAirports list shipped with the airports collector."""
    from config.settings import settings as app_settings

    path = app_settings.project_root / "ingestion" / "airports" / "europe_airports.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - mapping is best-effort
        return {}
    mapping: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        iata = str(row.get("iata") or "").strip().upper()
        icao = str(row.get("icao") or "").strip().upper()
        if iata and icao:
            mapping.setdefault(iata, icao)
    return mapping


def _timestamp(value: Any) -> str | None:
    """AirLabs returns 'YYYY-MM-DD HH:MM[:SS]' in UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[: len(fmt) + 6], fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    return None


class AirlabsSource(Source):
    """Scheduled movements with planned times and delays, within a call budget."""

    name = "airlabs"
    key = "flight_id"
    store_section = "flights"

    def __init__(
        self,
        app_settings=None,
        session: requests.Session | None = None,
        state_path: Path | None = None,
    ) -> None:
        super().__init__(app_settings)
        self._session = session or requests.Session()
        default_state = Path(self.settings.warehouse_dir) / "airlabs_state.json"
        self._state_path = Path(state_path or default_state)
        self._iata = _iata_to_icao()
        self._icao_to_iata = {icao: iata for iata, icao in self._iata.items()}

    # ── budget state ──────────────────────────────────────────────────────
    def _load_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - first run / unreadable
            return {}
        return state if isinstance(state, dict) else {}

    def _save_state(self, state: dict[str, Any]) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(state), encoding="utf-8")
            temp.replace(self._state_path)  # atomic: a crash cannot zero the counter
        except OSError as exc:
            logger.warning("[airlabs] could not persist budget state: %s", exc)

    # ── fetch ─────────────────────────────────────────────────────────────
    def _query(self, key: str, value: str) -> list[dict[str, Any]]:
        try:
            res = self._session.get(
                AIRLABS,
                params={
                    "api_key": self.settings.airlabs_api_key,
                    key: value,
                    "limit": 50,
                    "_fields": _FIELDS,
                },
                headers=HEADERS,
                timeout=self.settings.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.debug("[airlabs] %s=%s: %s", key, value, exc)
            return []
        if res.status_code != 200:
            logger.warning(
                "[airlabs] %s=%s -> HTTP %s %s", key, value, res.status_code, res.text[:160]
            )
            return []
        try:
            payload = res.json()
        except ValueError:
            return []
        rows = payload.get("response") if isinstance(payload, dict) else payload
        return rows if isinstance(rows, list) else []

    def _get(self, hub: str) -> list[dict[str, Any]]:
        """Schedules for a hub, by ICAO and falling back to IATA.

        The free key serves IATA fields; querying by ICAO can come back empty,
        which previously looked like "no flights" and silently starved the
        delay marts.
        """
        rows = self._query("dep_icao", hub)
        if rows:
            return rows
        iata = self._icao_to_iata.get(hub)
        if iata:
            rows = self._query("dep_iata", iata)
            if rows:
                logger.info(
                    "[airlabs] %s returned no rows for dep_icao; used dep_iata=%s", hub, iata
                )
                return rows
        logger.warning("[airlabs] %s returned no schedule rows by ICAO or IATA", hub)
        return []

    def _row(self, flight: dict[str, Any], hub: str, collected_at: str) -> dict[str, Any] | None:
        flight_code = str(flight.get("flight_icao") or flight.get("flight_iata") or "").strip()
        if not flight_code:
            return None
        departure = str(flight.get("dep_icao") or hub).upper()
        arrival = str(flight.get("arr_icao") or "").upper() or self._iata.get(
            str(flight.get("arr_iata") or "").strip().upper(), ""
        )
        status_raw = str(flight.get("status") or "scheduled").strip().lower()
        status = _STATUS.get(status_raw, "scheduled")
        delays = [
            value
            for value in (
                flight.get("arr_delayed"),
                flight.get("dep_delayed"),
                flight.get("delayed"),
            )
            if isinstance(value, (int, float))
        ]
        scheduled_departure = _timestamp(flight.get("dep_time_utc") or flight.get("dep_time"))
        token = (scheduled_departure or collected_at)[:16].replace(":", "").replace("-", "")
        return {
            "flight_id": f"airlabs_{flight_code}_{token}",
            "icao24": None,
            "callsign": flight_code,
            "airline_icao": flight_code[:3] if len(flight_code) >= 3 else None,
            "departure_icao": departure,
            "arrival_icao": arrival or None,
            "scheduled_departure": scheduled_departure,
            "scheduled_arrival": _timestamp(flight.get("arr_time_utc") or flight.get("arr_time")),
            "actual_departure": _timestamp(flight.get("dep_actual_utc")),
            "actual_arrival": _timestamp(flight.get("arr_actual_utc")),
            "status": status,
            "delay_minutes": max(0.0, float(max(delays))) if delays else 0.0,
            "cancelled": status == "cancelled",
            "source": "airlabs",
            "collected_at": collected_at,
        }

    def fetch(self) -> list[dict[str, Any]]:
        if not self.settings.airlabs_api_key:
            return []
        now = datetime.now(UTC)
        month = now.strftime("%Y-%m")
        state = self._load_state()
        if state.get("month") != month:
            state = {"month": month, "calls": 0, "hub_index": 0}
        budget = self.settings.airlabs_monthly_budget
        calls = int(state.get("calls") or 0)
        if calls >= budget:
            logger.warning(
                "[airlabs] monthly budget %s reached — skipping until next month", budget
            )
            return []

        hubs = TOP_AIRPORTS
        index = int(state.get("hub_index") or 0) % len(hubs)
        wanted = hubs[index : index + self.settings.airlabs_hubs_per_cycle]
        if len(wanted) < self.settings.airlabs_hubs_per_cycle:
            wanted += hubs[: self.settings.airlabs_hubs_per_cycle - len(wanted)]

        collected_at = now.isoformat()
        rows: list[dict[str, Any]] = []
        used = 0
        for hub in wanted:
            if calls + used >= budget:
                break
            flights = self._get(hub)
            used += 1  # the call was made whether or not it returned rows
            for flight in flights:
                row = self._row(flight, hub, collected_at)
                if row:
                    rows.append(row)

        state["calls"] = calls + used
        state["hub_index"] = (index + len(wanted)) % len(hubs)
        self._save_state(state)
        logger.info(
            "[airlabs] %d scheduled movements from %s (calls %s/%s this month)",
            len(rows),
            ", ".join(wanted),
            state["calls"],
            budget,
        )
        return rows
