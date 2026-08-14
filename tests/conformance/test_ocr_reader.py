"""PaddleOcrReader — the real OCR adapter, against a synthetic scanned
fixture with known ground truth.

Skipped by default. `PaddleOCR()` downloads model weights from a hoster on
first construction — `make ci` runs offline, so this cannot be part of the
default suite (`src/extraction/ocr.py`'s module docstring explains the same
constraint for a real ClamAV integration test, which this repo also does not
run by default). Opt in with `INAFIN_RUN_OCR_TESTS=1` after installing the
optional group: `uv pip install -e '.[ocr]'`.

**Run and verified manually this session (2026-08-14)**, not just written:
against real `paddleocr==3.7.0` + `paddlepaddle==3.3.1`, downloading actual
model weights over the network, `PaddleOcrReader` recovered the LUT
specimen's ARN (`AD2704240123456`) from `lut_scanned_synthetic.pdf`'s pixels
alone — `has_native_text is True`, `source == "ocr"`. See
`tests/fixtures/ocr/README.md` for how the fixture was built. The composition
rule (`FallbackPdfTextReader`) that decides WHEN this reader gets consulted
is tested separately, with fakes, in `test_pdf_text_fallback.py` — that file
runs in every `make ci`; this one does not.
"""

from __future__ import annotations

import os
import pathlib

import pytest

pytestmark = [
    pytest.mark.conformance,
    pytest.mark.skipif(
        os.environ.get("INAFIN_RUN_OCR_TESTS") != "1",
        reason=(
            "downloads real PaddleOCR model weights and requires the optional"
            " 'ocr' dependency group; opt in with INAFIN_RUN_OCR_TESTS=1"
        ),
    ),
]

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "ocr" / "lut_scanned_synthetic.pdf"


def test_paddle_ocr_reader_recovers_the_lut_arn_from_a_synthetic_scan() -> None:
    from src.extraction.ocr import PaddleOcrReader
    from src.extraction.reader import FallbackPdfTextReader, PypdfReader

    reader = FallbackPdfTextReader(PypdfReader(), PaddleOcrReader())
    result = reader.extract(FIXTURE.read_bytes())

    assert result.has_native_text is True
    assert result.source == "ocr"
    assert "AD2704240123456" in result.pages[0], (
        f"OCR did not recover the known LUT ARN; got: {result.pages[0][:500]!r}"
    )


def test_paddle_ocr_reader_reports_no_text_on_a_blank_page() -> None:
    """A genuinely blank page — the case OCR cannot help with either, so
    NoTextLayer must still be reachable through the OCR path."""
    import pypdf

    from src.extraction.ocr import PaddleOcrReader

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    import io

    buf = io.BytesIO()
    writer.write(buf)

    result = PaddleOcrReader().extract(buf.getvalue())
    assert result.has_native_text is False
