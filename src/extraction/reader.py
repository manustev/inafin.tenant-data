"""PDF text extraction port, and the one real adapter behind it.

Same idiom as `src/bronze/scan.py`'s `VirusScanPort`: a narrow `Protocol`,
one real adapter (`PypdfReader`), no OCR adapter yet. `HANDOFF-2026-08-11.md`
confirmed all 50 specimen PDFs have a native, machine-generated text layer —
none are scanned images — so there has never been a case in this workspace
that would exercise an OCR fallback. Building one now would be exactly the
"guessed layout, no ground truth" fiction `CLAUDE.md`'s "Why extraction
adapters are not next" already warns against, one level deeper (this time
about OCR fidelity rather than document layout). `has_native_text=False`
routes straight to `NoTextLayer` (`src/extraction/labelvalue.py`), the outcome
name `TODO.md` already agreed for the scanned-PDF case.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Protocol

import pypdf


@dataclass(frozen=True, slots=True)
class PdfText:
    """One PDF's extracted text, page by page.

    `has_native_text` is computed here rather than left for `labelvalue.py`
    to infer, because "is there anything to parse" is a property of the PDF
    itself, not of any one document type's label spec.
    """

    pages: list[str]
    has_native_text: bool


#: A page whose extracted text is shorter than this (after stripping) is
#: treated as empty. Chosen well below any real specimen's shortest page
#: (all 50 run to several hundred characters) and well above the few stray
#: characters a scanned PDF's embedded OCR layer or watermark can leave
#: behind — the threshold exists to separate "nothing to parse" from "a
#: one-line stub page", not to be a precise boundary.
_NATIVE_TEXT_MIN_CHARS = 20


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


class PypdfReader:
    """The one real adapter, over `pypdf`.

    `HANDOFF-2026-08-11.md` used `pypdf` for the inspection that unblocked
    this work; this class is where that throwaway choice becomes a real
    dependency (`pyproject.toml`). Nothing downstream imports `pypdf`
    directly — only this module does, matching `ClamAVScanner` being the only
    importer of `socket`/`struct` for its wire protocol.
    """

    def extract(self, data: bytes) -> PdfText:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        total_chars = sum(len(page.strip()) for page in pages)
        return PdfText(pages=pages, has_native_text=total_chars >= _NATIVE_TEXT_MIN_CHARS)
