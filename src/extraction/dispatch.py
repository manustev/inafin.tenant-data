"""run_extraction — PDF bytes in, an ExtractionOutcome (and a Silver write) out.

Looked up by `doc_type_code` against a runtime-built registry
(`src/extraction/registry.py`'s `build_extractor_registry`). This is NOT the
general Bronze->Silver dispatcher `TODO.md` priority 2 asks for — no
`doc_type_code -> loader class` table exists for the other archetypes'
CSV/NDJSON loaders either, and this module only resolves within the ~39 PDF
extractor types this batch builds. Called directly from tests and a manual
trigger for now, same as `RegisterLoader`/`EntitlementService` are today — no
HTTP route wired to it this session (`POST /artefacts/{id}/trigger` stays the
stub it already was).
"""

from __future__ import annotations

import uuid

from src.core.pool import TenantScopedPool
from src.core.tenant import TenantContext
from src.extraction.base import DocumentExtractor, SilverWriteResult
from src.extraction.labelvalue import ExtractionOutcome, NoTextLayer
from src.extraction.reader import PdfTextPort, PypdfReader
from src.extraction.registry import build_extractor_registry


class UnknownExtractorError(ValueError):
    """`doc_type_code` has no extractor registered — a real gap in this
    batch's coverage, not a validation event, so it is its own exception
    rather than `ValidationRejected`: nothing about the PDF's content was
    wrong, the platform simply has no adapter for it yet."""


async def run_extraction(
    ctx: TenantContext,
    pool: TenantScopedPool,
    *,
    ingest_id: uuid.UUID,
    entity_id: uuid.UUID,
    doc_type_code: str,
    pdf_bytes: bytes,
    ingest_run_id: uuid.UUID | None = None,
    reader: PdfTextPort | None = None,
    registry: dict[str, DocumentExtractor] | None = None,
) -> tuple[ExtractionOutcome, SilverWriteResult]:
    """Extract `pdf_bytes` as `doc_type_code` and write (or quarantine) it.

    Returns both the raw `ExtractionOutcome` and the `SilverWriteResult` —
    tests assert on the outcome (`Extracted`/named `Partial`/`NoTextLayer`)
    independently of whether the write path itself is being exercised.

    `registry` is an escape hatch, not a cache: pass a pre-built one (from a
    prior `build_extractor_registry` call) so a caller processing many
    artefacts in one request does not re-query `platform_ref.document_type`
    per artefact. Left unset, one is built fresh per call — the same
    per-call-not-cached trade `build_extractor_registry`'s own docstring
    explains.
    """
    registry = registry if registry is not None else await build_extractor_registry(pool, ctx)
    try:
        extractor = registry[doc_type_code]
    except KeyError:
        raise UnknownExtractorError(
            f"no extractor registered for {doc_type_code!r}"
        ) from None

    pdf_reader = reader or PypdfReader()
    pdf_text = pdf_reader.extract(pdf_bytes)

    outcome: ExtractionOutcome = (
        NoTextLayer() if not pdf_text.has_native_text else extractor.extract(pdf_text.pages)
    )

    write_result = await extractor.to_silver(
        outcome, ctx,
        entity_id=entity_id, ingest_id=ingest_id, pdf_bytes=pdf_bytes,
        ingest_run_id=ingest_run_id,
    )
    return outcome, write_result
