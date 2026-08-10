"""AuthPort — resolves an inbound request to a tenant, and the adapter behind it.

OVERVIEW. `AuthPort` is the contract the API layer depends on: one method,
`resolve(request) -> TenantContext`. One adapter implements it today —
`StaticTokenAuth`, a bearer-token -> slug lookup read from `Settings`.

WHY A PORT, NOT A HEADER READ IN THE ROUTE HANDLER. ARCHITECTURE.md 5.6:
tenant identity comes from a signed Keycloak JWT claim and is **never** a
caller-supplied parameter — "the gateway rejects any request whose body
disagrees." That is real infrastructure (JWKS fetch, signature verification,
claim extraction) that nobody has stood up yet. Writing this as a `Protocol`
means the eventual Keycloak adapter is a second class satisfying the same
contract, not a rewrite of every route handler — the same reason
`src/bronze/scan.py`'s `VirusScanPort` exists instead of importing `clamd`
directly into `BronzeIngestionService`.

WHAT `StaticTokenAuth` IS NOT. It is a placeholder, not a security boundary.
A bearer token that maps 1:1 to a tenant slug is closer to the target shape
than a request accepting `?slug=` directly (the slug is never read from
anything the caller wrote freeform), but there is no signature, no
expiry, no revocation — anyone holding a token can act as that tenant
indefinitely. Do not deploy this adapter anywhere internet-facing.
"""

from __future__ import annotations

from typing import Protocol

from starlette.requests import Request

from src.core.config import Settings
from src.core.errors import AuthenticationError
from src.core.tenant import TenantContext


class AuthPort(Protocol):
    """What the API needs to turn a request into a tenant, and nothing more."""

    def resolve(self, request: Request) -> TenantContext:
        """Resolve `request` to the tenant making it.

        Raises `AuthenticationError` for anything that isn't a good credential
        for a known tenant — a missing header, a malformed token, a token
        that maps to nothing. Never returns a `TenantContext` built from
        anything the request body or query string supplied directly; that is
        exactly the caller-supplied-parameter shape ARCHITECTURE.md 5.6
        forbids.
        """
        ...


class StaticTokenAuth:
    """Bearer token -> tenant slug, via a static map read from `Settings`.

    The map is `token -> slug`, not `slug -> token`: a lookup by the value the
    request actually presents, so a request cannot claim a slug and have that
    claim merely checked against a matching token — it can only ever resolve
    to whichever tenant its specific token was issued for.
    """

    def __init__(self, settings: Settings) -> None:
        self._tokens = settings.api_tenant_tokens

    def resolve(self, request: Request) -> TenantContext:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("missing or malformed Authorization header")

        slug = self._tokens.get(token)
        if slug is None:
            raise AuthenticationError("token does not resolve to a known tenant")

        return TenantContext(slug=slug)
