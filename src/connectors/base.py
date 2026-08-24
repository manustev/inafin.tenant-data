"""`SourceConnectorPort` — the one contract every Category B source is pulled
through, and the two exceptions callers must distinguish.

Every connector, live or fixture-backed, answers the same question: "give me
the bytes for this document type, for this tenant, for this GSTIN and
period." What differs between adapters is entirely how that question gets
answered (an HTTP call, a portal scrape, a file on disk) — never the shape of
the question or the answer, which is why this is a `Protocol` with one
method rather than a base class with hooks.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """What one `fetch()` call produced — the bytes plus enough provenance
    to hand straight to `BronzeIngestionService.receive` and to explain,
    later, where the bytes actually came from.

    Attributes:
        content: The raw bytes, exactly as the source produced them —
            unwrapped of any transport envelope (see `adapters/gstn_api.py`'s
            docstring on the GSTN API's `status_cd`/`rek`/base64+AES
            envelope; unwrapping that is this layer's job, not Bronze's).
        filename: A synthetic filename carrying the real extension
            (`.json`/`.csv`/`.pdf`) so `src/bronze/filecheck.py`'s
            extension vocabulary and `src/dispatch/content_format.py`'s
            inference both work unchanged on connector output.
        content_format: One of `"json" | "csv" | "pdf"` — reuses
            `src/bronze/filecheck.py:ALLOWED_EXTENSIONS`' vocabulary
            directly rather than inventing a second one.
        source_ref: What was actually pulled from — a fixture file path in
            local-fixture mode, an API call id / portal document id in live
            mode. Recorded so a later "where did this artefact come from"
            question has an answer that survives a provider swap, the same
            reasoning `ScanResult.scanner` already uses.
        fetched_at: When this connector produced the bytes — NOT when GSTN
            generated the underlying return (`gendt` inside a GSTR-2B
            payload, for instance, which is business content and belongs to
            whatever parses `content`, not to this envelope).
    """

    content: bytes
    filename: str
    content_format: str
    source_ref: str
    fetched_at: dt.datetime


class ConnectorNotConfiguredError(RuntimeError):
    """A live connector was asked to fetch, but this deployment has not
    configured credentials/a base URL for its source system.

    Raised instead of returning an empty or fabricated response — an
    unconfigured connector must fail loudly, the same reasoning
    `ScannerUnavailableError` is deliberately not swallowed in
    `src/bronze/service.py`. Wiring real credentials later turns this into a
    real fetch without any caller changing.
    """


class SourceDocumentNotFoundError(FileNotFoundError):
    """Local-fixture mode: no file under the configured fixture root matches
    the requested (tenant, ref, gstin, period). Distinct from
    `ConnectorNotConfiguredError` — this means the connector IS working, the
    sample data just doesn't have this combination."""


class SourceConnectorPort(Protocol):
    """One method every adapter — live or fixture — implements identically.

    `ref` (e.g. `"B1.03"`) rather than `doc_type_code` alone is passed
    through explicitly because fixture-mode folder layout is organised by
    `ref` (`registry/document_types.csv`'s own primary key), and a live
    adapter may need it too — GSTN's actual API endpoints differ by return
    type, not just by `doc_type_code` string.

    `gstin`/`period` are optional: register-shaped documents (an open-SCN
    register, a registration-amendment history) are not scoped to one GSTIN
    or one period, and the connector call for those omits both.
    """

    async def fetch(
        self,
        *,
        tenant_slug: str,
        doc_type_code: str,
        ref: str,
        gstin: str | None = None,
        period: str | None = None,
    ) -> FetchedDocument:
        """Raises `ConnectorNotConfiguredError` (live adapters, no
        credentials yet) or `SourceDocumentNotFoundError` (fixture adapter,
        no matching file)."""
        ...
