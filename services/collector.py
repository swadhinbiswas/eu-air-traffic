"""Always-on collector service for a VPS — the single edge node.

One long-lived process polls every upstream aviation API, publishes normalised
records to Kafka, feeds the in-memory :class:`~services.live_store.LiveStore`,
and serves the live snapshot API the dashboard consumes:

    adsb.lol / OpenSky  → positions      ─┐
    OpenSky             → flights         │
    aviationweather     → METAR / TAF     ├─▶ Kafka (Aiven)  → sink → Bronze Parquet → Hugging Face
    Open-Meteo          → forecast        │
    AviationStack       → fuel            ├─▶ LiveStore → GET /live/snapshot → React dashboard
    reference           → airports/…      ┘

Run::

    python -m services.collector               # all loops + live API, forever
    python -m services.collector --once        # single pass, then exit
    python -m services.collector --no-api      # headless (Kafka only)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import time
from typing import Any

from config.logging import logger, setup_logging
from config.settings import Settings, settings
from services.bronze import BronzeWriter
from services.enrichment import enrich_position
from services.kafka_bus import KafkaBus
from services.live_store import LiveStore
from services.sources import Source, build_sources


class CollectorService:
    """Runs every source loop, the Kafka producer and the live API."""

    def __init__(
        self, app_settings: Settings | None = None, sources: list[Source] | None = None
    ) -> None:
        self.settings = app_settings or settings
        self.sources = sources or build_sources(self.settings)
        self.bus = KafkaBus(self.settings)
        self.bronze = BronzeWriter(self.settings)
        self.store = LiveStore()
        self._running = False
        self._api_server: Any = None
        self._last_publish: dict[str, float] = {}
        self.stats: dict[str, int] = {"kafka": 0, "bronze": 0, "live": 0}

    # ── intervals ──────────────────────────────────────────────────────────
    def _interval(self, source: Source) -> float:
        return float(getattr(self.settings, f"{source.name}_interval_seconds", 300.0))

    def _publish_due(self, source: Source) -> bool:
        """Positions update the live store every tick but only reach the lake on
        a slower cadence; other sources publish every cycle."""
        if source.name != "positions":
            return True
        interval = self.settings.positions_publish_interval_seconds
        now = time.monotonic()
        if now - self._last_publish.get(source.name, 0.0) >= interval:
            self._last_publish[source.name] = now
            return True
        return False

    # ── routing ────────────────────────────────────────────────────────────
    def _handle(self, source: Source, records: list[dict[str, Any]], publish: bool = True) -> int:
        if not records:
            return 0
        if source.name == "reference":
            self.store.update_reference(records)
        else:
            self.store.update(source.name, records, source.key)
        self.stats["live"] += len(records)

        if not publish:
            return 0

        if self.bus.available:
            sent = self.bus.produce(source.topic, records, key=source.key)
            self.stats["kafka"] += sent
        else:
            # No broker: still persist locally so a sink can pick it up later.
            if self.bronze.write(source.name, records):
                self.stats["bronze"] += len(records)
        logger.info("[collector] %s → %s rows (%s)", source.name, len(records), source.topic)
        return len(records)

    def poll_once(self, source: Source, publish: bool | None = None) -> int:
        records = source.fetch()
        if source.name == "positions" and records:
            records = [enrich_position(row) for row in records]
        return self._handle(
            source, records, publish=self._publish_due(source) if publish is None else publish
        )

    # ── loops ──────────────────────────────────────────────────────────────
    async def _source_loop(self, source: Source) -> None:
        interval = self._interval(source)
        while self._running:
            try:
                await asyncio.to_thread(self.poll_once, source)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                logger.error("[collector] %s cycle failed: %s", source.name, exc)
            await asyncio.sleep(interval)

    async def _serve_api(self) -> None:
        import uvicorn

        from services.live_api import create_app

        app = create_app(self.store, self.settings)
        config = uvicorn.Config(
            app,
            host=self.settings.live_api_host,
            port=self.settings.live_api_port,
            log_level=self.settings.log_level.lower(),
            access_log=False,
        )
        self._api_server = uvicorn.Server(config)
        # The service owns signal handling; uvicorn must not hijack SIGTERM or
        # the source loops never learn to stop (they would run forever).
        self._api_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        logger.info(
            "[collector] live API on http://%s:%s/live/snapshot",
            self.settings.live_api_host,
            self.settings.live_api_port,
        )
        await self._api_server.serve()

    async def run(self, serve_api: bool = True) -> None:
        self._running = True
        self.bus.connect()
        logger.info(
            "[collector] starting — %s sources, kafka=%s, api=%s",
            len(self.sources),
            self.bus.available,
            serve_api,
        )
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()

        def _request_stop() -> None:
            logger.info("[collector] stop requested")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _request_stop)

        tasks = [asyncio.create_task(self._source_loop(source)) for source in self.sources]
        if serve_api:
            tasks.append(asyncio.create_task(self._serve_api()))

        async def _wait_stop() -> None:
            await stop_event.wait()

        tasks.append(asyncio.create_task(_wait_stop()))

        try:
            # Finish as soon as a stop signal arrives; cancel the rest.
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._running = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.stop()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._api_server is not None:
            self._api_server.should_exit = True
        logger.info("[collector] stopping — stats=%s", self.stats)
        self.bus.close()

    def run_once(self) -> dict[str, int]:
        self.bus.connect()
        try:
            for source in self.sources:
                self.poll_once(source)
        finally:
            self.bus.close()
        return self.stats


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="EU air-traffic collector service (VPS)")
    parser.add_argument("--once", action="store_true", help="single pass, then exit")
    parser.add_argument("--no-api", action="store_true", help="disable the live API server")
    args = parser.parse_args()

    service = CollectorService()
    if args.once:
        stats = service.run_once()
        print(stats)
        return 0 if (stats["kafka"] or stats["bronze"]) else 1

    try:
        asyncio.run(service.run(serve_api=not args.no_api))
    except KeyboardInterrupt:
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
