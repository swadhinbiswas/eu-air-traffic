"""Application settings, loaded from environment variables and `.env` files.

All secrets live in environment variables / `.env` (never in source code).

The platform has three planes:

* **Edge (VPS)** — the always-on :mod:`services.collector` polls every upstream
  aviation API and publishes normalised records to Kafka, while serving a single
  live snapshot API to the dashboard.
* **Lake (Hugging Face)** — :mod:`services.sink` drains Kafka into immutable
  Bronze Parquet, and the GitHub Actions workflows transform Bronze → Silver and
  publish both layers back to the dataset.
* **Warehouse (MotherDuck)** — dbt builds the Gold marts that back the
  analytics, stories, catalog, explorer, SQL, ops and docs pages.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
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

    # ── Hugging Face dataset (the data lake) ───────────────────────────────
    # ``HF_TOKEN`` / ``HF_REPO`` are the documented names; the longer aliases
    # keep backwards compatibility with older deployments.
    huggingface_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("HF_TOKEN", "HUGGINGFACE_TOKEN"),
    )
    huggingface_repo: str = Field(
        default="swadhinbiswas/air-traffic",
        validation_alias=AliasChoices("HF_REPO", "HUGGINGFACE_REPO"),
    )
    hf_bronze_prefix: str = Field(default="bronze")
    hf_silver_prefix: str = Field(default="silver")
    hf_gold_prefix: str = Field(default="gold")
    hf_private: bool = Field(default=True)

    # ── Kafka event bus (Aiven) ──────────────────────────────────────────
    aiven_kafka_host: str | None = Field(default=None)
    aiven_kafka_port: int = Field(default=12345, ge=1)
    aiven_kafka_username: str | None = Field(default=None)
    aiven_kafka_password: str | None = Field(default=None)
    aiven_kafka_ca_cert: str | None = Field(
        default=None, description="Path to the Aiven CA certificate (.pem)"
    )
    kafka_security_protocol: str = Field(
        default="SASL_SSL", description="SASL_SSL for Aiven; PLAINTEXT for local Redpanda"
    )
    kafka_sasl_mechanism: str = Field(default="PLAIN")

    # One topic per data product. The three weather streams share a single
    # topic (split downstream by the record ``_kind``) so the whole platform
    # fits in 5 topics on small Kafka plans.
    kafka_topic_positions: str = Field(default="eu-positions")
    kafka_topic_flights: str = Field(default="eu-flights")
    kafka_topic_weather: str = Field(default="eu-weather")
    kafka_topic_fuel: str = Field(default="eu-fuel")
    kafka_topic_reference: str = Field(default="eu-reference")

    # ── Kafka sink (Kafka → Bronze Parquet → Hugging Face) ───────────────
    sink_consumer_group: str = Field(default="eu-air-traffic-sink")
    sink_batch_size: int = Field(default=5_000, ge=1)
    sink_poll_timeout_ms: int = Field(default=30_000, ge=1_000)
    sink_max_seconds: float = Field(
        default=0.0, ge=0.0, description="Stop after N seconds (0 = run until idle)"
    )

    # ── MotherDuck (Gold warehouse / dbt target) ─────────────────────────
    motherduck_token: str | None = Field(default=None)
    motherduck_database: str = Field(default="air_traffic")
    # Where the pipeline *builds* the star schema + dbt models. Keep "local"
    # for reproducible builds and publish to MotherDuck afterwards; set
    # "motherduck" to build straight into the cloud database.
    warehouse_target: Literal["local", "motherduck"] = "local"

    # ── Turso (derived edge serving layer) ───────────────────────────────
    # Gold marts + precomputed site payloads are published here after dbt and
    # read by the site for fast, always-current analytics. One-way and derived:
    # MotherDuck remains authoritative.
    turso_database_url: str | None = Field(
        default=None, description="libSQL/Turso URL, e.g. libsql://my-db-org.turso.io"
    )
    turso_auth_token: str | None = Field(default=None)
    turso_serving_enabled: bool = Field(default=True)
    turso_sync_tables: str = Field(
        default="gold_airport_metrics,gold_airline_rankings,gold_delay_analysis,"
        "gold_weather_impact,gold_seasonal_trends,gold_fuel_price_series,"
        "gold_aircraft_class_mix,fact_positions,dim_airport,dim_aircraft,dim_route,"
        "fact_emissions,fact_flights,weather",
        description="Comma-separated warehouse tables copied to Turso",
    )

    # ── Live API (single endpoint served from the VPS collector) ─────────
    live_api_host: str = Field(default="0.0.0.0")
    live_api_port: int = Field(default=8090, ge=1, le=65535)
    live_api_public_url: str | None = Field(
        default=None,
        description="Public base URL the dashboard should call, e.g. https://live.example.com",
    )
    live_api_token: str | None = Field(
        default=None, description="Optional bearer token required for non-read endpoints"
    )
    live_snapshot_max_age_seconds: float = Field(default=120.0, ge=5.0)

    # ── Collector service tuning ─────────────────────────────────────────
    positions_interval_seconds: float = Field(default=15.0, ge=5.0)
    # Live positions hit the dashboard every 15s, but only this often to the
    # lake — persisting every tick is ~17M rows/day and mostly unused.
    positions_publish_interval_seconds: float = Field(default=300.0, ge=30.0)
    flights_interval_seconds: float = Field(default=1800.0, ge=300.0)
    # /flights/all window; OpenSky rejects anything above two hours.
    flights_lookback_minutes: int = Field(default=90, ge=30, le=120)
    # Arrivals are published in a nightly batch, so backfill them rarely.
    flights_arrival_interval_seconds: float = Field(default=21600.0, ge=3600.0)
    # AirLabs schedules: the free key allows 1,000 calls a month and 50 rows per
    # call, so a rotating pair of hubs every six hours stays inside budget.
    airlabs_api_key: str | None = Field(default=None)
    airlabs_interval_seconds: float = Field(default=21600.0, ge=3600.0)
    airlabs_hubs_per_cycle: int = Field(default=4, ge=1, le=8)
    airlabs_monthly_budget: int = Field(default=800, ge=0)
    metar_interval_seconds: float = Field(default=300.0, ge=60.0)
    taf_interval_seconds: float = Field(default=900.0, ge=60.0)
    forecast_interval_seconds: float = Field(default=3600.0, ge=300.0)
    fuel_interval_seconds: float = Field(default=86_400.0, ge=3_600.0)
    reference_interval_seconds: float = Field(default=86_400.0, ge=3_600.0)

    # Concurrent upstream fetches (I/O-bound, so modest counts keep a 2-core
    # VPS comfortable without hammering the free APIs).
    adsb_max_workers: int = Field(default=4, ge=1, le=32)
    flights_max_workers: int = Field(default=4, ge=1, le=32)
    # Busiest hubs polled for movements (OpenSky anonymous credits are limited).
    flights_airports: int = Field(default=12, ge=1, le=48)
    kafka_flush_timeout_seconds: float = Field(default=15.0, ge=1.0)

    # ── Kafka batch window (GitHub Actions pulls ~every 9 minutes) ────────
    # The sink resumes from its committed offset, so each run drains exactly the
    # records accumulated since the previous run. Kafka topic retention must be
    # longer than the schedule gap or records are lost.
    lake_window_seconds: int = Field(default=540, ge=60)  # 9 minutes
    kafka_retention_hours: int = Field(default=24, ge=1)

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
    realtime_db_path: Path = PROJECT_ROOT / "warehouse" / "realtime.duckdb"
    dbt_project_dir: Path = PROJECT_ROOT / "dbt"

    model_config = SettingsConfigDict(
        env_file=(".env", str(PROJECT_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    @field_validator("environment", mode="before")
    @classmethod
    def _normalise_environment(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("huggingface_repo", mode="before")
    @classmethod
    def _strip_huggingface_repo(cls, value: object) -> object:
        # A single trailing space in the GitHub variable made create_repo reject
        # the id and every upload fail, while the step still reported success.
        return value.strip() if isinstance(value, str) else value

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
        self.realtime_db_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def motherduck_enabled(self) -> bool:
        """True when a MotherDuck token is configured (Gold serving layer)."""
        return bool(self.motherduck_token)

    @property
    def motherduck_connection(self) -> str:
        """DuckDB connection string for the MotherDuck database."""
        return f"md:{self.motherduck_database}?motherduck_token={self.motherduck_token}"

    @property
    def warehouse_connection(self) -> str:
        """Connection the pipeline builds into.

        Local DuckDB by default (reproducible builds, single-writer, no cloud
        round-trips); MotherDuck only when ``WAREHOUSE_TARGET=motherduck``.
        """
        if self.warehouse_target == "motherduck" and self.motherduck_enabled:
            return self.motherduck_connection
        return str(self.duckdb_path)

    @property
    def serving_connection(self) -> str:
        """Connection read by the API / BI: MotherDuck when configured, else local."""
        if self.motherduck_enabled:
            return self.motherduck_connection
        return str(self.duckdb_path)

    @property
    def kafka_enabled(self) -> bool:
        if not self.aiven_kafka_host:
            return False
        if self.kafka_security_protocol == "PLAINTEXT":
            return True
        return bool(self.aiven_kafka_username and self.aiven_kafka_password)

    @property
    def kafka_topics(self) -> dict[str, str]:
        """All Kafka topics, keyed by topic role."""
        return {
            "positions": self.kafka_topic_positions,
            "flights": self.kafka_topic_flights,
            "weather": self.kafka_topic_weather,
            "fuel": self.kafka_topic_fuel,
            "reference": self.kafka_topic_reference,
        }

    def topic_for_source(self, name: str) -> str:
        """Resolve a collector source name to its Kafka topic.

        The weather sources share one topic; records carry ``_kind`` so the
        sink can split them back into datasets.
        """
        if name in ("metar", "taf", "forecast"):
            return self.kafka_topic_weather
        if name in ("flights", "airlabs"):
            # Schedules are movements; they share the flights topic and dataset.
            return self.kafka_topic_flights
        topics = self.kafka_topics
        if name in topics:
            return topics[name]
        raise KeyError(f"Unknown source for Kafka routing: {name!r}")

    @property
    def live_api_enabled(self) -> bool:
        return bool(self.live_api_public_url) or self.environment != "production"

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
            "airlabs": bool(self.airlabs_api_key),
            "motherduck": self.motherduck_enabled,
            "kafka": self.kafka_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


settings = get_settings()
