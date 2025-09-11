"""Learned callsign → route memory.

Scheduled flights reuse flight numbers on fixed city pairs, so once we have
seen a callsign depart A and later arrive B, every future sighting of that
callsign can show the full route — including at cruise altitude where geometry
alone yields nothing. Memory is a plain JSON file under the checkpoints dir,
capped in size, and safe to delete (it simply relearns).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config.logging import logger

ANCHOR_TTL_SECONDS = 12 * 3600.0
MAX_CALLSIGNS = 3000


def _format_route(origin: str | None, destination: str | None) -> str | None:
    if origin and destination:
        return f"{origin} → {destination}"
    if origin:
        return f"{origin} → ?"
    if destination:
        return f"? → {destination}"
    return None


class RouteMemory:
    """Callsign route pairs learned from our own observations."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        # callsign → {"pairs": {"A->B": count}, "anchor": A|None, "anchor_at": ts,
        #             "updated": ts}
        self._callsigns: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._last_save = 0.0
        if self.path is not None:
            self.load()

    # ── persistence ──────────────────────────────────────────────────────────
    def load(self) -> None:
        if self.path is None:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._callsigns = payload
        except (OSError, ValueError):
            self._callsigns = {}

    def save(self, force: bool = False) -> None:
        if self.path is None or (not force and not self._dirty):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._callsigns), encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = False
            self._last_save = time.monotonic()
        except OSError as exc:
            logger.warning("[routes] could not persist route memory: %s", exc)

    def maybe_save(self, interval_seconds: float = 300.0) -> None:
        if self._dirty and time.monotonic() - self._last_save >= interval_seconds:
            self.save()

    # ── learning ─────────────────────────────────────────────────────────────
    def _entry(self, callsign: str) -> dict[str, Any]:
        entry = self._callsigns.get(callsign)
        if entry is None:
            entry = {"pairs": {}, "anchor": None, "anchor_at": 0.0, "updated": 0.0}
            self._callsigns[callsign] = entry
            if len(self._callsigns) > MAX_CALLSIGNS:
                oldest = min(self._callsigns, key=lambda k: self._callsigns[k].get("updated", 0.0))
                del self._callsigns[oldest]
        return entry

    def observe(
        self,
        callsign: str | None,
        origin: str | None,
        destination: str | None,
        now: float | None = None,
    ) -> None:
        """Record one estimated leg. Completes A→B pairs from anchor history."""
        if not callsign:
            return
        cs = callsign.strip().upper()
        if not cs:
            return
        ts = now if now is not None else time.time()
        entry = self._entry(cs)

        anchor = entry.get("anchor")
        anchor_at = float(entry.get("anchor_at") or 0.0)
        if anchor and ts - anchor_at > ANCHOR_TTL_SECONDS:
            anchor, entry["anchor"] = None, None

        if origin and origin != destination:
            # Departing/ground at a new airport: complete any open pair first.
            if anchor and anchor != origin:
                self._add_pair(entry, anchor, origin)
            entry["anchor"] = origin
            entry["anchor_at"] = ts
        elif destination:
            if anchor and anchor != destination:
                self._add_pair(entry, anchor, destination)
                entry["anchor"] = destination
                entry["anchor_at"] = ts
            elif not anchor:
                entry["anchor"] = destination
                entry["anchor_at"] = ts
        entry["updated"] = ts
        self._dirty = True

    def _add_pair(self, entry: dict[str, Any], origin: str, destination: str) -> None:
        pairs = entry.setdefault("pairs", {})
        key = f"{origin}->{destination}"
        pairs[key] = int(pairs.get(key, 0)) + 1

    # ── resolving ────────────────────────────────────────────────────────────
    def _best_pair(
        self, callsign: str, origin: str | None = None, destination: str | None = None
    ) -> tuple[str | None, str | None]:
        entry = self._callsigns.get(callsign.strip().upper())
        if not entry:
            return None, None
        pairs: dict[str, int] = entry.get("pairs") or {}
        best: tuple[str | None, str | None] | None = None
        best_count = 0
        for key, count in pairs.items():
            try:
                leg_o, leg_d = key.split("->")
            except ValueError:
                continue
            if origin and leg_o != origin:
                continue
            if destination and leg_d != destination:
                continue
            if count > best_count:
                best, best_count = (leg_o, leg_d), count
        return best if best else (None, None)

    def resolve(
        self, callsign: str | None, origin: str | None, destination: str | None
    ) -> dict[str, Any]:
        """Fill a full route for display.

        Returns ``route``/``route_origin``/``route_destination``/``route_source``
        where source is ``typical`` (learned pair) or ``estimated`` (single
        observed leg) — never invented.
        """
        mem_o, mem_d = (None, None)
        if callsign:
            mem_o, mem_d = self._best_pair(callsign, origin, destination)
        final_o = origin or mem_o
        final_d = destination or mem_d
        source = "typical" if (mem_o or mem_d) and (final_o and final_d) else "estimated"
        if not final_o and not final_d:
            source = "none"
        return {
            "route": _format_route(final_o, final_d),
            "route_origin": final_o,
            "route_destination": final_d,
            "route_source": source,
        }
