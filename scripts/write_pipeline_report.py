"""Write the lake's own pipeline report for the Ops page.

The Ops dashboard reads ``warehouse/checkpoints/pipeline_report.json``; only
the local orchestrator used to produce it, so the hosted pipeline always showed
"no recent run". If this script runs, every earlier step in the workflow
succeeded — that is exactly what the report says.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from config.logging import logger
from config.settings import settings

STEPS = [
    "Pull existing Silver from Hugging Face",
    "Sink Kafka → Bronze Parquet",
    "Silver transform (incremental)",
    "Publish Silver to Hugging Face",
    "Build DuckDB star schema",
    "dbt build (Gold models + tests)",
    "Register gold_* views",
    "Publish Gold to MotherDuck",
    "Quality report",
    "Publish site tables to MotherDuck",
]


def main() -> int:
    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY")
    finished = datetime.now(UTC)
    report = {
        "success": True,
        "started_at": finished.isoformat(),
        "finished_at": finished.isoformat(),
        "run_id": run_id,
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if run_id and repo else None,
        "steps": [{"name": name, "status": "ok"} for name in STEPS],
    }
    path = Path(settings.checkpoint_dir) / "pipeline_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    logger.info("[ops] pipeline report written → %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
