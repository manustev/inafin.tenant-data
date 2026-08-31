"""`NoAuth` / `build_auth` — the dev-only bypass added so a local developer
can call `/artefacts` without managing a bearer token (`AUTH_MODE=none`).

`static_token` (the default) is unaffected — the existing `StaticTokenAuth`
tests in `tests/conformance/test_api_ingest.py` prove that path unchanged, so
these tests are scoped to the new adapter and the factory's selection logic
only.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from src.api.auth import NoAuth, StaticTokenAuth, build_auth
from src.core.config import Settings
from src.core.errors import AuthenticationError

pytestmark = pytest.mark.conformance


def _request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    scope = {"type": "http", "headers": raw_headers}
    return Request(scope)


def _settings(**overrides: object) -> Settings:
    return Settings(
        pg_app_dsn="postgresql://x/y", pg_migrate_dsn="postgresql://x/y",
        **overrides,  # type: ignore[arg-type]
    )


def test_build_auth_defaults_to_static_token() -> None:
    assert isinstance(build_auth(_settings()), StaticTokenAuth)


def test_build_auth_returns_no_auth_only_when_explicitly_set() -> None:
    assert isinstance(build_auth(_settings(auth_mode="none")), NoAuth)


def test_no_auth_resolves_with_no_header_at_all() -> None:
    auth = NoAuth(_settings(auth_mode="none", dev_tenant_slug="acme"))
    ctx = auth.resolve(_request())
    assert ctx.slug == "acme"


def test_no_auth_honours_dev_tenant_slug() -> None:
    auth = NoAuth(_settings(auth_mode="none", dev_tenant_slug="globex"))
    ctx = auth.resolve(_request())
    assert ctx.slug == "globex"


def test_no_auth_still_resolves_a_valid_token_to_its_own_tenant() -> None:
    """The override path: a developer testing cross-tenant behaviour can
    still pass a token and get THAT tenant, not the configured default."""
    auth = NoAuth(_settings(
        auth_mode="none", dev_tenant_slug="acme",
        api_tenant_tokens={"tok-globex": "globex"},
    ))
    ctx = auth.resolve(_request({"Authorization": "Bearer tok-globex"}))
    assert ctx.slug == "globex"


def test_no_auth_still_rejects_a_bad_token_rather_than_falling_back() -> None:
    """A malformed/unrecognised credential must not silently succeed as the
    default tenant — "you sent something and it was wrong" stays an error."""
    auth = NoAuth(_settings(auth_mode="none", dev_tenant_slug="acme"))
    with pytest.raises(AuthenticationError):
        auth.resolve(_request({"Authorization": "Bearer not-a-real-token"}))
