"""Application settings, loaded from environment variables and `.env` files.

All secrets live in environment variables / `.env` (never in source code).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # ── External API credentials (keep in .env, never commit) ──────────────
    aviationstack_api_key: str | None = Field(default=None)
    openweather_api_key: str | None = Field(default=None)
    airportdb_api_token: str | None = Field(default=None)
    opensky_username: str | None = Field(default=None)
    opensky_password: str | None = Field(default=None)
    opensky_client_id: str | None = Field(default=None)
    opensky_client_secret: str | None = Field(default=None)
    huggingface_token: str | None = Field(default=None)
    huggingface_repo: str = Field(default="air-traffic-warehouse/air-traffic")

    # ── Runtime behaviour ──────────────────────────────────────────────────
    environment: Literal["development", "test", "production"] = "development"
    mock_mode: bool = Field(
        default=False,
        description=(
            "When True, collectors fall back to deterministic synthetic data when "
            "API credentials are unavailable. Ideal for local demos and CI."
        ),
    )
    log_level: str = Field(default="INFO")

    # ── HTTP client tuning ─────────────────────────────────────────────────
    request_timeout_seconds: float = Field(default=15.0, ge=1.0)
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_base: float = Field(default=2.0, ge=1.0)
    rate_limit_delay_seconds: float = Field(default=0.25, ge=0.0)
    openweather_limit: int = Field(default=60, ge=1)  # requests/minute

    # ── Pipeline ───────────────────────────────────────────────────────────
    pipeline_batch_size: int = Field(default=50_000, ge=1)
    delay_threshold_minutes: int = Field(default=15, ge=0)  # OTP tolerance
    europe_icao_prefixes: tuple[str, ...] = (
        "BI",
        "EF",
        "EN",
        "ES",
        "EK",
        "EG",
        "EI",
        "EB",
        "EH",
        "EL",
        "LF",
        "LS",
        "ED",
        "ET",
        "LO",
        "LK",
        "LZ",
        "LH",
        "EP",
        "LE",
        "GC",
        "LP",
        "LI",
        "LM",
        "LG",
        "LC",
        "LA",
        "LD",
        "LJ",
        "LQ",
        "LY",
        "LW",
        "LB",
        "LR",
        "LU",
        "EE",
        "EV",
        "EY",
        "UK",
        "UM",
        "UU",
        "UL",
        "UB",
        "UD",
        "UG",
        "LT",
    )

    # ── Storage layout (Medallion architecture) ────────────────────────────
    project_root: Path = PROJECT_ROOT
    warehouse_dir: Path = PROJECT_ROOT / "warehouse"
    raw_dir: Path = PROJECT_ROOT / "warehouse" / "raw"
    bronze_dir: Path = PROJECT_ROOT / "warehouse" / "bronze"
    silver_dir: Path = PROJECT_ROOT / "warehouse" / "silver"
    gold_dir: Path = PROJECT_ROOT / "warehouse" / "gold"
    quarantine_dir: Path = PROJECT_ROOT / "warehouse" / "quarantine"
    checkpoint_dir: Path = PROJECT_ROOT / "warehouse" / "checkpoints"
    duckdb_path: Path = PROJECT_ROOT / "warehouse" / "air_traffic.duckdb"
    dbt_project_dir: Path = PROJECT_ROOT / "dbt"

    model_config = SettingsConfigDict(
        env_file=(".env", str(PROJECT_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # pydantic-settings maps field names to env vars automatically
        # (e.g. ``openweather_api_key`` → ``OPENWEATHER_API_KEY``).
    )

    @field_validator("environment", mode="before")
    @classmethod
    def _normalise_environment(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        return value.strip().upper()

    def ensure_directories(self) -> None:
        """Create the full Medallion storage tree if it does not exist."""
        for directory in (
            self.raw_dir,
            self.bronze_dir,
            self.silver_dir,
            self.gold_dir,
            self.quarantine_dir,
            self.checkpoint_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def credentials_available(self) -> dict[str, bool]:
        """Report which upstream data sources we have credentials for."""
        return {
            "aviationstack": bool(self.aviationstack_api_key),
            "openweather": bool(self.openweather_api_key),
            "airportdb": bool(self.airportdb_api_token),
            "opensky": bool(
                (self.opensky_username and self.opensky_password)
                or (self.opensky_client_id and self.opensky_client_secret)
            ),
            "huggingface": bool(self.huggingface_token),
        }


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


settings = get_settings()
