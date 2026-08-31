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

    # --- API auth (src/api/auth.py) ---
    # token -> tenant slug. A placeholder adapter, not a security boundary:
    # ARCHITECTURE.md 5.6 wants tenant identity from a signed Keycloak JWT
    # claim, never a caller-supplied value. This is closer to that than a raw
    # slug parameter, but real JWT/JWKS verification is unbuilt — see
    # src/api/auth.py's module docstring. Read from env as a JSON object,
    # e.g. API_TENANT_TOKENS='{"dev-acme-token": "acme"}'.
    api_tenant_tokens: dict[str, str] = Field(default_factory=dict)

    # "static_token" (default) or "none" — the explicit off switch, same
    # named-mode idiom as virus_scan_provider/source_data_mode: a string a
    # deployment sets on purpose, never a bool that silently defaults to
    # "safe" or "off" depending which way someone reads it. "none" builds
    # `NoAuth` instead of `StaticTokenAuth` — see that class's docstring for
    # exactly what it does and does not protect. This exists for local dev
    # only; nothing about it is meant to reach a shared or deployed
    # environment, and src/api/app.py logs an unmissable warning on startup
    # whenever it is active.
    auth_mode: str = "static_token"

    # Which tenant AUTH_MODE=none resolves every unauthenticated request to.
    # Ignored entirely when auth_mode is "static_token".
    dev_tenant_slug: str = "acme"

    # --- OCR fallback (src/extraction/ocr.py) ---
    # Off by default — `paddleocr`/`paddlepaddle` are an optional dependency
    # group (`pip install .[ocr]`), not a core one, and `PaddleOCR()` pulls
    # model weights from a hoster on first construction. `make ci` runs
    # offline, so the default here must stay False; a deployment that wants
    # the fallback opts in explicitly, same as `virus_scan_provider`.
    ocr_enabled: bool = False

    # --- Category B source connectors (src/connectors/) ---
    # "local_fixture" (default): every doc_type_code's fetch() is answered
    # from source_fixture_root instead of a live HTTP call — no GSP/ICEGATE/
    # DGFT/IRP/EWB-portal credentials exist in this workspace yet. "live"
    # routes through src/connectors/factory.py's per-source_system adapter,
    # each of which raises ConnectorNotConfiguredError until its entry in
    # the two dicts below is populated. Same on/off idiom as
    # virus_scan_provider: the flag is read once, in factory.py, never
    # branched on anywhere else.
    source_data_mode: str = "local_fixture"
    source_fixture_root: str = "fixtures/bronze_source"
    # source_system -> base URL / Vault secret reference. Both empty by
    # default for every key; a deployment configures one source system at a
    # time as GSP/ICEGATE/DGFT/IRP contracts are actually signed. Read as a
    # JSON object, e.g.
    # SOURCE_CONNECTOR_BASE_URLS='{"GSTN_API": "https://gsp.example.com"}'.
    source_connector_base_urls: dict[str, str] = Field(default_factory=dict)
    source_connector_credential_refs: dict[str, str] = Field(default_factory=dict)

    environment: str = "development"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
