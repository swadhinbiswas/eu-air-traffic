"""Publish the lake's freshness metrics to CloudWatch.

The freshness alarm (``infra/terraform/observability.tf``) is the one that
catches the failure this platform actually hits: every process healthy, every
upstream key still accepted, and the numbers quietly going stale. The alarm
watches a custom metric — this script emits it.

It runs at the very end of the lake cycle, so if it runs at all every earlier
step succeeded. ``LastSuccessfulCycleAgeSeconds`` is the age of the *previous*
successful cycle, which is what "is the data current?" means to a reader. It
carries **no dimensions**, matching the alarm.

    python -m scripts.publish_metrics                 # emit to CloudWatch
    python -m scripts.publish_metrics --dry-run       # print the payload

Best-effort by design: a metric-publish failure must not fail the lake cycle,
whose data has already been written. The alarm's ``treat_missing_data =
"notBreaching"`` means a failed publish cannot page anyone falsely.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.logging import logger
from config.settings import settings

DEFAULT_NAMESPACE = "EUAirTraffic/Lake"
AGE_METRIC = "LastSuccessfulCycleAgeSeconds"
SUCCESS_METRIC = "CycleSuccess"
DURATION_METRIC = "CycleDurationSeconds"


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def build_metrics(
    report: dict[str, Any] | None, now: datetime | None = None
) -> dict[str, tuple[float, str]]:
    """Turn a pipeline report into ``{metric_name: (value, unit)}``.

    The age is measured from the *previous* successful run to ``now``; the
    duration from this run's own start/finish. Pure, so it is unit-testable
    without touching CloudWatch.
    """
    now = now or datetime.now(UTC)
    metrics: dict[str, tuple[float, str]] = {}
    report = report or {}
    finished = _parse(report.get("finished_at"))
    if finished is not None:
        metrics[AGE_METRIC] = (max(0.0, (now - finished).total_seconds()), "Seconds")
    started = _parse(report.get("started_at"))
    if finished is not None and started is not None:
        metrics[DURATION_METRIC] = (
            max(0.0, (finished - started).total_seconds()),
            "Seconds",
        )
    metrics[SUCCESS_METRIC] = (1.0, "Count")
    return metrics


def _read_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        logger.warning("[metrics] no pipeline report at %s — emitting success only", path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("[metrics] unreadable pipeline report: %s", exc)
        return None


def publish(
    namespace: str | None = None,
    *,
    report_path: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Emit the metrics. Returns the number written (0 when dry-run or empty)."""
    path = report_path or (Path(settings.checkpoint_dir) / "pipeline_report.json")
    namespace = namespace or DEFAULT_NAMESPACE
    metrics = build_metrics(_read_report(path))
    if not metrics:
        return 0

    payload = [
        {"MetricName": name, "Value": value, "Unit": unit}
        for name, (value, unit) in metrics.items()
    ]
    if dry_run:
        print(json.dumps({"Namespace": namespace, "MetricData": payload}, indent=1))
        return 0

    import boto3  # imported lazily so the module is importable without the aws extra

    client = boto3.client("cloudwatch", region_name=settings.aws_region)
    client.put_metric_data(Namespace=namespace, MetricData=payload)
    logger.info(
        "[metrics] published %s metrics to %s: %s",
        len(payload),
        namespace,
        ", ".join(f"{name}={value:.0f}" for name, (value, _) in metrics.items()),
    )
    return len(payload)


def main() -> int:
    from config.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="Publish lake freshness metrics to CloudWatch")
    parser.add_argument(
        "--namespace",
        default=None,
        help=f"CloudWatch namespace (default: {DEFAULT_NAMESPACE})",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the payload and exit")
    args = parser.parse_args()
    try:
        publish(args.namespace, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - never fail the lake cycle over metrics
        logger.warning("[metrics] publish failed (non-fatal): %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
