"""Live API — the single read endpoint for the dashboard.

Served by the VPS collector from the shared :class:`~services.live_store.LiveStore`
so the React app never calls adsb.lol/OpenSky/aviationweather directly (they
block browser CORS) and never needs a separate edge Worker. All routes are
CORS-open; ``/live/snapshot`` is the one call the dashboard needs.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from config.settings import Settings, settings
from services.live_store import LiveStore


def create_app(store: LiveStore, app_settings: Settings | None = None) -> FastAPI:
    """Build the live FastAPI app around a store instance."""
    cfg = app_settings or settings
    app = FastAPI(
        title="EU Air Traffic — Live API",
        version="2.0.0",
        description="Single live snapshot for the EU air-traffic dashboard.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _no_store(request: Any, call_next: Any) -> Any:
        """Never let a browser or proxy cache live data.

        Without an explicit directive browsers may reuse a response, which shows
        up as the dashboard freezing on the same snapshot for minutes.
        """
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response

    def _snapshot(slim: bool = False) -> dict[str, Any]:
        return store.snapshot(cfg.live_snapshot_max_age_seconds, slim=slim)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "environment": cfg.environment,
            "mock_mode": cfg.mock_mode,
            "credentials": cfg.credentials_available,
            "live": store.health(cfg.live_snapshot_max_age_seconds),
        }

    @app.get("/live/status")
    def status() -> dict[str, Any]:
        return store.health(cfg.live_snapshot_max_age_seconds)

    @app.get("/live/snapshot")
    def snapshot() -> dict[str, Any]:
        return _snapshot()

    @app.get("/live/positions")
    def positions(limit: int = Query(default=8000, ge=1, le=50_000)) -> dict[str, Any]:
        data = _snapshot()
        return {
            "generatedAt": data["generatedAt"],
            "count": len(data["positions"]),
            "aircraft": data["positions"][:limit],
        }

    @app.get("/live/flights")
    def flights(limit: int = Query(default=2000, ge=1, le=20_000)) -> dict[str, Any]:
        data = _snapshot()
        return {"generatedAt": data["generatedAt"], "flights": data["flights"][:limit]}

    @app.get("/live/weather")
    def weather() -> dict[str, Any]:
        data = _snapshot()
        return {"generatedAt": data["generatedAt"], **data["weather"]}

    @app.get("/live/fuel")
    def fuel() -> dict[str, Any]:
        data = _snapshot()
        return {"generatedAt": data["generatedAt"], "fuel": data["fuel"]}

    @app.get("/live/taf")
    def taf(ids: str = Query(default="")) -> dict[str, Any]:
        """TAF rows in the dashboard's camelCase contract."""
        data = _snapshot()
        wanted = {i.strip().upper() for i in ids.split(",") if i.strip()}
        rows = data["weather"]["taf"]
        if wanted:
            rows = [r for r in rows if str(r.get("station_icao", "")).upper() in wanted]
        return {
            "source": "vps",
            "generatedAt": data["generatedAt"],
            "taf": [
                {
                    "icao": r.get("station_icao"),
                    "issueTime": r.get("issue_time"),
                    "validFrom": r.get("valid_from"),
                    "validTo": r.get("valid_to"),
                    "rawTAF": r.get("raw_taf"),
                }
                for r in rows
            ],
        }

    @app.get("/live/aircraft/{hex}")
    def aircraft(hex: str) -> dict[str, Any]:
        """Enrichment for one airframe from its live position row."""
        target = hex.upper()
        for position in store.section("positions"):
            if str(position.get("icao24", "")).upper() != target:
                continue
            return {
                "icao24": target,
                "found": True,
                "registration": position.get("registration"),
                "type": position.get("aircraft_type"),
                "typeName": position.get("type_name"),
                "manufacturer": position.get("manufacturer"),
                "operator": position.get("operator_name"),
                "operatorCountry": position.get("operator_country"),
                "aircraftClass": position.get("aircraft_class"),
                "emitterClass": position.get("emitter_class"),
                "wakeCategory": position.get("wake_category"),
                "route": position.get("route"),
                "co2KgPerHour": position.get("co2_kg_per_hour"),
            }
        return {"icao24": target, "found": False}

    @app.get("/live/emissions")
    def emissions() -> dict[str, Any]:
        data = _snapshot()
        return {"generatedAt": data["generatedAt"], **data["emissions"]}

    @app.get("/live/reference/{kind}")
    def reference(kind: str) -> dict[str, Any]:
        allowed = {"airports", "routes", "aircraft", "emission_factors", "holidays"}
        if kind not in allowed:
            raise HTTPException(status_code=404, detail=f"unknown reference kind {kind!r}")
        data = _snapshot()
        return {"generatedAt": data["generatedAt"], kind: data["reference"].get(kind, [])}

    return app


# Standalone app (empty until a collector fills a store) for `uvicorn services.live_api:app`.
app = create_app(LiveStore())
