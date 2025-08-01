"""Pipeline orchestrator — the sequential DAG run by CI / cron / API.

Flow (matches the documented Medallion architecture):

    collect → bronze → silver → gold → warehouse (DuckDB) → optional HF upload

Each step is isolated and idempotent. A failure in a non-critical collector is
logged and skipped (graceful degradation); a failure in a data-processing step
aborts the run with a non-zero exit code so the scheduler can alert.

Run with: ``python -m pipelines.orchestrator``
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.logging import logger
from config.settings import Settings, settings

# Steps that may be skipped gracefully when upstreams are unavailable.
_OPTIONAL_COLLECTORS = ("weather", "flights", "fuel")


@dataclass
class PipelineReport:
    """Structured result of a pipeline run."""

    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    elapsed_seconds: float = 0.0
    steps: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "success": self.success,
            "steps": self.steps,
            "errors": self.errors,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def _step(report: PipelineReport, name: str, fn: Any) -> None:
    start = time.perf_counter()
    try:
        result = fn()
        report.steps[name] = {
            "status": "ok",
            "result": result,
            "elapsed_seconds": round(time.perf_counter() - start, 2),
        }
    except Exception as exc:  # noqa: BLE001
        elapsed = round(time.perf_counter() - start, 2)
        report.steps[name] = {"status": "failed", "elapsed_seconds": elapsed}
        report.errors[name] = str(exc)
        if name in _OPTIONAL_COLLECTORS:
            logger.warning("[orchestrator] optional step %s failed — continuing: %s", name, exc)
        else:
            report.success = False
            logger.error("[orchestrator] step %s failed: %s", name, exc, exc_info=True)


class Orchestrator:
    def __init__(self, app_settings: Settings | None = None) -> None:
        self.settings = app_settings or settings

    def collect(self) -> dict[str, int]:
        from ingestion.registry import available, create

        counts: dict[str, int] = {}
        for name in available():
            collector = create(name, app_settings=self.settings)
            counts[name] = collector.run()
        return counts

    def run(self) -> PipelineReport:
        report = PipelineReport()
        logger.info("=== pipeline run starting (mock_mode=%s) ===", self.settings.mock_mode)

        _step(report, "collect", self.collect)
        if report.success:
            _step(report, "bronze", self._bronze)
        if report.success:
            _step(report, "silver", self._silver)
        if report.success:
            _step(report, "gold", self._gold)
        if report.success:
            _step(report, "warehouse", self._warehouse)
        if report.success:
            _step(report, "quality", self._quality)
        if report.success and self.settings.huggingface_token:
            _step(report, "upload_hf", self._upload_hf)
        else:
            report.steps["upload_hf"] = {"status": "skipped", "result": "HF_TOKEN not set"}

        report.finished_at = datetime.now(UTC).isoformat()
        report.elapsed_seconds = round(
            (
                datetime.fromisoformat(report.finished_at)
                - datetime.fromisoformat(report.started_at)
            ).total_seconds(),
            2,
        )
        report.success = report.success and not report.errors

        report_path = self.settings.checkpoint_dir / "pipeline_report.json"
        report.save(report_path)
        logger.info(
            "=== pipeline finished: success=%s elapsed=%.2fs ===",
            report.success,
            report.elapsed_seconds,
        )
        return report

    # ── step implementations ──────────────────────────────────────────────
    def _bronze(self) -> dict[str, int]:
        from pipelines.bronze import persist_all

        return persist_all(app_settings=self.settings)

    def _silver(self) -> dict[str, int]:
        from pipelines.silver import run_all

        return run_all(app_settings=self.settings)

    def _gold(self) -> dict[str, int]:
        from pipelines.gold import build_marts

        return build_marts(self.settings)

    def _warehouse(self) -> dict[str, int]:
        from pipelines.warehouse import build

        return build(self.settings)

    def _quality(self) -> dict[str, Any]:
        from pipelines.quality import quality_report

        return quality_report(self.settings)

    @staticmethod
    def _upload_hf() -> dict[str, Any]:
        from scripts.upload_hf import upload_warehouse

        return upload_warehouse()


def main() -> int:
    orchestrator = Orchestrator()
    report = orchestrator.run()
    if report.success:
        logger.info("All steps completed successfully.")
        return 0
    logger.error(
        "Pipeline finished with %s failed step(s): %s", len(report.errors), list(report.errors)
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
