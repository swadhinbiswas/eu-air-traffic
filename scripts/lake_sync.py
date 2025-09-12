"""Lake sync — move Bronze/Silver between the local workspace and Hugging Face.

The GitHub Actions jobs are stateless, so each one pulls the layer it needs,
transforms it, and pushes the result back:

    python -m scripts.lake_sync pull-bronze    # HF bronze/  → warehouse/bronze/
    python -m scripts.lake_sync push-silver    # warehouse/silver/ → HF silver/
    python -m scripts.lake_sync pull-silver    # HF silver/ → warehouse/silver/
    python -m scripts.lake_sync status
"""

from __future__ import annotations

import argparse
import sys

from config.logging import logger, setup_logging
from config.settings import settings
from services import hf_lake


def _assert_not_mock(action: str) -> None:
    """Mock data must never reach the lake.

    A CI run with MOCK_MODE=true once defaulted onto the schedule and uploaded
    synthetic flights/weather, which then failed the dbt build downstream.
    """
    if settings.mock_mode:
        raise SystemExit(
            f"[lake_sync] refusing to {action} with MOCK_MODE=true — "
            "the lake must only ever contain real data."
        )


def pull_bronze() -> int:
    return hf_lake.download_prefix(
        f"{settings.hf_bronze_prefix}/parquet", settings.warehouse_dir, app_settings=settings
    )


def push_silver() -> int:
    _assert_not_mock("push silver")
    return hf_lake.upload_directory(
        settings.silver_dir, settings.hf_silver_prefix, app_settings=settings
    )


def pull_silver() -> int:
    return hf_lake.download_prefix(
        settings.hf_silver_prefix, settings.warehouse_dir, app_settings=settings
    )


def status() -> int:
    enabled = hf_lake.hf_enabled(settings)
    print(f"hf_enabled={enabled} repo={settings.huggingface_repo}")
    print(f"bronze_prefix={settings.hf_bronze_prefix} silver_prefix={settings.hf_silver_prefix}")
    return 0


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Sync Bronze/Silver with Hugging Face")
    parser.add_argument("action", choices=["pull-bronze", "push-silver", "pull-silver", "status"])
    args = parser.parse_args()
    actions = {
        "pull-bronze": pull_bronze,
        "push-silver": push_silver,
        "pull-silver": pull_silver,
        "status": status,
    }
    count = actions[args.action]()
    logger.info("[lake_sync] %s done (%s)", args.action, count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
