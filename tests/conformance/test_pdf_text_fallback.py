"""FallbackPdfTextReader's composition rule — native first, OCR only on a
miss — tested against fakes, not the real PaddleOCR adapter.

This is deliberately separate from `test_ocr_reader.py`: the routing logic
(when does the secondary reader get consulted, what does `NoTextLayer`
require now) is real production logic that must run in every `make ci`, but
proving it works needs no real OCR engine — a fake `PdfTextPort` is the
"legitimate, ground-truth-controlled" fixture (CLAUDE.md's own phrase for
this kind of choice), same reasoning `scripts/gen_mock_erp.py` gets applied
to structured ERP data. `PaddleOcrReader` itself, against a real synthetic
scan, is `test_ocr_reader.py` — opt-in, since it downloads model weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest

from src.extraction.reader import FallbackPdfTextReader, PdfText

pytestmark = pytest.mark.conformance


@dataclass(frozen=True, slots=True)
class _FakeReader:
    """A `PdfTextPort` that returns a fixed, pre-baked result — no PDF
    parsing, no OCR, just the composition contract."""

    result: PdfText
    calls: list[bytes]

    def extract(self, data: bytes) -> PdfText:
        self.calls.append(data)
        return self.result


def _fake(
    *, has_native_text: bool, pages: list[str], source: Literal["native", "ocr"] = "native",
) -> _FakeReader:
    return _FakeReader(
        result=PdfText(pages=pages, has_native_text=has_native_text, source=source),
        calls=[],
    )


def test_native_text_short_circuits_the_secondary() -> None:
    primary = _fake(has_native_text=True, pages=["real text"])
    secondary = _fake(has_native_text=True, pages=["should never be seen"])

    result = FallbackPdfTextReader(primary, secondary).extract(b"pdf-bytes")

    assert result.pages == ["real text"]
    assert result.source == "native"
    assert secondary.calls == [], "secondary must not run when the primary finds text"


def test_no_native_text_falls_through_to_the_secondary() -> None:
    primary = _fake(has_native_text=False, pages=[""])
    secondary = _fake(has_native_text=True, pages=["ocr recovered text"])

    result = FallbackPdfTextReader(primary, secondary).extract(b"pdf-bytes")

    assert result.pages == ["ocr recovered text"]
    assert result.has_native_text is True
    assert result.source == "ocr"
    assert secondary.calls == [b"pdf-bytes"]


def test_no_secondary_configured_behaves_exactly_like_the_primary_alone() -> None:
    """Today's unchanged behaviour: OCR disabled (Settings.ocr_enabled=False,
    the default) must not alter anything about the no-OCR case."""
    primary = _fake(has_native_text=False, pages=[""])

    result = FallbackPdfTextReader(primary, None).extract(b"pdf-bytes")

    assert result.has_native_text is False
    assert result.source == "native"


def test_no_text_layer_still_fires_when_ocr_also_finds_nothing() -> None:
    """The resolved open question: NoTextLayer now means 'no native layer
    AND OCR unavailable-or-also-failed', not a second, separate concept."""
    primary = _fake(has_native_text=False, pages=[""])
    secondary = _fake(has_native_text=False, pages=[""])

    result = FallbackPdfTextReader(primary, secondary).extract(b"pdf-bytes")

    assert result.has_native_text is False
    assert secondary.calls == [b"pdf-bytes"], "the secondary must still have been tried"
