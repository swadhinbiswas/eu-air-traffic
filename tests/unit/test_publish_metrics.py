"""Unit tests for the CloudWatch freshness-metric emitter."""

from __future__ import annotations

from datetime import UTC, datetime

import boto3
import pytest

from scripts.publish_metrics import (
    AGE_METRIC,
    DURATION_METRIC,
    SUCCESS_METRIC,
    build_metrics,
    publish,
)

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402


def test_build_metrics_from_report():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    report = {
        "success": True,
        "started_at": "2026-01-01T11:50:00+00:00",
        "finished_at": "2026-01-01T11:55:00+00:00",
    }
    metrics = build_metrics(report, now=now)
    assert metrics[AGE_METRIC][0] == 300.0  # 5 minutes stale
    assert metrics[DURATION_METRIC][0] == 300.0
    assert metrics[SUCCESS_METRIC][0] == 1.0


def test_build_metrics_without_report_only_reports_success():
    metrics = build_metrics(None)
    assert set(metrics) == {SUCCESS_METRIC}


def test_build_metrics_accepts_zulu_timestamps():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    metrics = build_metrics({"finished_at": "2026-01-01T11:00:00Z"}, now=now)
    assert metrics[AGE_METRIC][0] == 3600.0


@mock_aws
def test_publish_puts_metric_data(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")

    report = tmp_path / "pipeline_report.json"
    report.write_text(
        '{"started_at": "2026-01-01T11:54:00+00:00", "finished_at": "2026-01-01T11:55:00+00:00"}',
        encoding="utf-8",
    )

    written = publish("EUAirTraffic/Test", report_path=report)
    assert written == 3

    client = boto3.client("cloudwatch", region_name="eu-central-1")
    names = {
        metric["MetricName"]
        for metric in client.list_metrics(Namespace="EUAirTraffic/Test")["Metrics"]
    }
    assert {AGE_METRIC, DURATION_METRIC, SUCCESS_METRIC} <= names
