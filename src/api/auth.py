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

A SECOND ADAPTER, `NoAuth`, exists purely so a local developer can call
`/artefacts` without managing a bearer token at all — this is the "second
class satisfying the same `Protocol`" the paragraph above was written to
make cheap. `build_auth` below is the one place that chooses between the
two, reading `Settings.auth_mode`; nothing else in the API layer branches
on which adapter is active.
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


class NoAuth:
    """DEV-ONLY: skip authentication, resolving every request to one fixed
    tenant (`Settings.dev_tenant_slug`) — for `AUTH_MODE=none`.

    WHY THIS DOES NOT REOPEN ARCHITECTURE.md 5.6. That rule is about tenant
    identity never coming from anything the CALLER supplies in the request
    (a `?slug=`, a body field a client could set to any value). The tenant
    this resolves to is fixed by the process's OWN configuration, set once at
    startup by whoever runs the server — not by anything in the request. A
    caller cannot use this mode to reach a tenant other than the one
    configured; that is a materially different, and much narrower, thing than
    what 5.6 forbids.

    OPTIONAL TOKEN OVERRIDE. If the request DOES carry a well-formed
    `Authorization: Bearer <token>` header, it is still resolved through the
    same static map `StaticTokenAuth` uses — so a developer testing
    cross-tenant behaviour locally can still switch tenants by passing a
    token, while every other request needs nothing at all. A malformed or
    unrecognised token still raises `AuthenticationError` rather than
    silently falling back to the default tenant; "you passed something that
    looks like a credential and it was wrong" must not quietly succeed.

    WHAT THIS IS NOT. Still not a security boundary, more so than
    `StaticTokenAuth`: literally nothing is required to act as
    `dev_tenant_slug`. `src/api/app.py` logs an unmissable warning banner on
    startup whenever this adapter is active, and `auth_mode` defaults to
    `"static_token"` — this only ever runs when someone has deliberately set
    `AUTH_MODE=none` in their own environment.
    """

    def __init__(self, settings: Settings) -> None:
        self._default = TenantContext(slug=settings.dev_tenant_slug)
        self._static = StaticTokenAuth(settings)

    def resolve(self, request: Request) -> TenantContext:
        if request.headers.get("authorization"):
            return self._static.resolve(request)
        return self._default


def build_auth(settings: Settings) -> AuthPort:
    """The one place `Settings.auth_mode` is read. See `NoAuth`'s docstring
    before ever setting `AUTH_MODE=none` outside a local machine."""
    if settings.auth_mode == "none":
        return NoAuth(settings)
    return StaticTokenAuth(settings)
