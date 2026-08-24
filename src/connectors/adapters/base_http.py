"""`HttpSourceConnector` — shared shape for every live adapter in this
package.

Each concrete adapter differs only in `source_system` (the registry value it
answers for) and `_do_fetch` (the real HTTP call, once credentials exist).
The "refuse until configured" behaviour, and what "configured" means, is
identical across all seven — factored here once rather than repeated per
adapter, the same way `DocumentExtractor`'s archetype base classes in
`src/extraction/base.py` factor `to_silver()`'s common shape and leave only
the per-type rule to the subclass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from src.connectors.base import ConnectorNotConfiguredError, FetchedDocument


@dataclass(frozen=True, slots=True)
class SourceCredential:
    """A reference to where real credentials live, never the credentials
    themselves. `secret_ref` is a Vault path (or equivalent) resolved at
    call time by whatever secret-fetching this deployment already has — this
    repo has no secret manager wired in yet, so today this class only ever
    carries the sentinel "unconfigured" state (`base_url == ""`), which
    `HttpSourceConnector._configured` treats as "do not attempt a call."""

    secret_ref: str


class HttpSourceConnector(ABC):
    """Base class for every live, HTTP-backed connector.

    Subclasses set `source_system` (must match a
    `platform_ref.document_type.source_system` value exactly — `factory.py`
    keys its dispatch table on this) and implement `_do_fetch`, the real API
    call. `fetch()` itself is not overridden by subclasses: the
    configured-or-refuse gate must run identically for all seven, or a
    future adapter could accidentally skip it.
    """

    source_system: ClassVar[str]

    def __init__(self, *, base_url: str, credential: SourceCredential | None) -> None:
        self._base_url = base_url
        self._credential = credential

    def _configured(self) -> bool:
        """True once both a base URL and a credential reference are set.
        Deliberately not "base_url is a valid URL" or "credential resolves
        to a live secret" — this class has no way to check either without
        making the very call it is guarding, so it only checks that a
        deployment has actually tried to configure it."""
        return bool(self._base_url) and self._credential is not None

    async def fetch(
        self,
        *,
        tenant_slug: str,
        doc_type_code: str,
        ref: str,
        gstin: str | None = None,
        period: str | None = None,
    ) -> FetchedDocument:
        if not self._configured():
            raise ConnectorNotConfiguredError(
                f"{type(self).__name__} ({self.source_system}) has no base_url/"
                f"credential configured — set it via Settings before fetching "
                f"{ref!r} for tenant {tenant_slug!r} live. Until then, run "
                f"with source_data_mode='local_fixture'."
            )
        return await self._do_fetch(
            tenant_slug=tenant_slug, doc_type_code=doc_type_code, ref=ref,
            gstin=gstin, period=period,
        )

    @abstractmethod
    async def _do_fetch(
        self,
        *,
        tenant_slug: str,
        doc_type_code: str,
        ref: str,
        gstin: str | None,
        period: str | None,
    ) -> FetchedDocument:
        """The real HTTP call. Every concrete subclass overrides this with
        NotImplementedError today (credentials do not exist to test
        against) — `fetch()` above cannot reach this until `_configured()`
        is true, so the NotImplementedError is inert until a real deployment
        configures a base URL and gets past the gate, at which point it is a
        precise TODO marker for exactly what remains."""
