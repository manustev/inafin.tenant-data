"""PDF text extraction port, and its adapters.

Same idiom as `src/bronze/scan.py`'s `VirusScanPort`: a narrow `Protocol`,
a primary real adapter (`PypdfReader`). `HANDOFF-2026-08-11.md` confirmed all
50 specimen PDFs have a native, machine-generated text layer — none are
scanned images — so nothing in this workspace's fixtures exercises an OCR
fallback path with real ground truth. `src/extraction/ocr.py`'s
`PaddleOcrReader` and this module's `FallbackPdfTextReader` close that gap:
`FallbackPdfTextReader` composes a primary reader with an optional secondary,
tried only when the primary finds no native text. This resolves the open
question `HANDOFF-2026-08-11.md` left unanswered — what `NoTextLayer` means
once OCR exists: it now means "no native layer AND OCR unavailable or also
found nothing", not a new, separate concept. `PdfText.source` carries which
one actually produced the text, so a caller (or the future
`extraction_audit` table this session does not build) can weight OCR-sourced
text differently — OCR is lossy in a way a native layer is not (a misread
digit in a GSTIN is a compliance-grade error), so provenance stays visible
rather than folded away.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal, Protocol

import pypdf


@dataclass(frozen=True, slots=True)
class PdfText:
    """One PDF's extracted text, page by page.

    `has_native_text` is computed here rather than left for `labelvalue.py`
    to infer, because "is there anything to parse" is a property of the PDF
    itself, not of any one document type's label spec. Despite the name, it
    is really "is there usable text at all" once a `source="ocr"` result can
    also produce it — see `FallbackPdfTextReader`.
    """

    pages: list[str]
    has_native_text: bool
    source: Literal["native", "ocr"] = "native"


#: A page whose extracted text is shorter than this (after stripping) is
#: treated as empty. Chosen well below any real specimen's shortest page
#: (all 50 run to several hundred characters) and well above the few stray
#: characters a scanned PDF's embedded OCR layer or watermark can leave
#: behind — the threshold exists to separate "nothing to parse" from "a
#: one-line stub page", not to be a precise boundary.
NATIVE_TEXT_MIN_CHARS = 20


class PdfTextPort(Protocol):
    """What extraction needs from a PDF reader, and nothing more."""

    def extract(self, data: bytes) -> PdfText:
        """Read `data` as a PDF and return its per-page text.

        Must not raise for a PDF that merely has no text layer — that is
        `PdfText(pages=..., has_native_text=False)`, a normal outcome, not an
        error. Raising is reserved for bytes that are not a parseable PDF at
        all, which is an intake-shape problem (`src/bronze/filecheck.py`) the
        caller did not already rule out.
        """
        ...


class UnreadablePdfError(ValueError):
    """The bytes handed to a PDF_EXTRACTION trigger are not a parseable PDF.

    A `ValueError` subclass, the same shape `UnknownExtractorError`
    (`src/extraction/dispatch.py`) and `infer_content_format`'s plain
    `ValueError` already use for "the caller's request cannot be satisfied as
    asked" — this is that same category, not Silver's judgement about a
    document's business content (`ValidationRejected`, which quarantines).
    Nothing about pypdf's exception hierarchy is a fact this repo owns, so it
    is translated at the one place that imports `pypdf` rather than leaking
    `pypdf.errors.PyPdfError` to a caller three layers away.

    Defense in depth, not the fix for the ERP upload E2E suite's finding
    (2026-09-01): the actual bug was `src/catalogue/document_schema.py`
    publishing `TABULAR` for five `PDF_EXTRACTION` types, so a tenant
    following the (now corrected) published contract never reaches this path
    with CSV bytes. This catches the case that fix does not — a genuine PDF
    upload that is truncated, corrupt, or otherwise not a valid PDF at all —
    which is possible for any `PDF_EXTRACTION` type regardless of the
    catalogue being right, and previously reached the caller as an opaque
    HTTP 500 (`pypdf.errors.PdfStreamError` uncaught, three layers up).
    """


class PypdfReader:
    """The one real adapter, over `pypdf`.

    `HANDOFF-2026-08-11.md` used `pypdf` for the inspection that unblocked
    this work; this class is where that throwaway choice becomes a real
    dependency (`pyproject.toml`). Nothing downstream imports `pypdf`
    directly — only this module does, matching `ClamAVScanner` being the only
    importer of `socket`/`struct` for its wire protocol.
    """

    def extract(self, data: bytes) -> PdfText:
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
        except pypdf.errors.PyPdfError as exc:
            # PyPdfError is the common base for PdfStreamError, PdfReadError,
            # EmptyFileError and ParseError — every shape "these bytes are not
            # a PDF" takes. NOT DependencyError/DeprecationError, which are
            # this repo's own setup problems, not the caller's.
            raise UnreadablePdfError(
                f"could not read PDF: {exc}"
            ) from exc
        total_chars = sum(len(page.strip()) for page in pages)
        return PdfText(pages=pages, has_native_text=total_chars >= NATIVE_TEXT_MIN_CHARS)


class FallbackPdfTextReader:
    """Native text first, OCR only if the primary finds none.

    Implements `PdfTextPort` itself, so `src/extraction/dispatch.py`'s
    `run_extraction(reader=...)` parameter needs no new branch — passing one
    of these IS how OCR gets wired in; passing nothing (the default,
    `PypdfReader()`) is exactly today's behaviour, unchanged.
    """

    def __init__(self, primary: PdfTextPort, secondary: PdfTextPort | None = None) -> None:
        self._primary = primary
        self._secondary = secondary

    def extract(self, data: bytes) -> PdfText:
        primary_result = self._primary.extract(data)
        if primary_result.has_native_text or self._secondary is None:
            return primary_result

        secondary_result = self._secondary.extract(data)
        if not secondary_result.has_native_text:
            # Neither reader found usable text — NoTextLayer still fires
            # downstream, now honestly meaning "and OCR didn't help either".
            return primary_result
        return PdfText(
            pages=secondary_result.pages, has_native_text=True, source="ocr",
        )
