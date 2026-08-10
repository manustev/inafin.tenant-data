"""Configuration. Three Postgres DSNs, deliberately.

The separation is a security control, not tidiness:

  PG_APP_DSN     — through PgBouncer, as app_login. The ONLY DSN the runtime
                   may use. NOINHERIT, so it can do nothing until SET LOCAL ROLE.
  PG_MIGRATE_DSN — direct, as tenant_migrate. DDL and provisioning only. Never
                   held by a request-serving process.
  PG_SUPER_DSN   — direct, superuser. Bootstrap, and the conformance suite's
                   privileged control connection (which proves that the rows a
                   tenant cannot see actually exist). Never in a deployed app.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Postgres ---
    pg_app_dsn: str = Field(
        description="Through PgBouncer, as app_login. The only runtime DSN."
    )
    pg_migrate_dsn: str = Field(
        description="Direct, as tenant_migrate. DDL and provisioning only."
    )
    pg_super_dsn: str = Field(
        default="",
        description="Direct, superuser. Bootstrap and test control only.",
    )

    app_pool_min_size: int = 1
    app_pool_max_size: int = 10

    # --- Object store (Bronze) ---
    s3_endpoint_url: str = ""
    s3_region: str = "ap-south-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_prefix: str = "inafin-tenant"
    # CGST s.36: books must be retained 72 months from the due date of the
    # annual return for the year concerned. 2190d ~ 72 months, applied as an
    # Object Lock retention so it cannot be undone by a misconfigured
    # lifecycle policy — or by root.
    bronze_retention_days: int = 2190

    # --- Kafka (the doorbell) ---
    kafka_bootstrap: str = "localhost:9092"
    kafka_batch_topic: str = "inafin.tenant.batch_ready"
    kafka_enabled: bool = True

    # --- Virus scan (Bronze intake) ---
    # "none" is the explicit off switch (src.bronze.scan.NullScanner), never a
    # silent default that happens to skip scanning. This is a provider name,
    # not a library import, precisely so the scanner can be swapped — a
    # commercial API, a cloud-native scanner, or turned off per environment —
    # without touching src/bronze/service.py. See src/bronze/scan.py.
    virus_scan_provider: str = "none"
    clamav_host: str = "localhost"
    clamav_port: int = 3310

    environment: str = "development"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
