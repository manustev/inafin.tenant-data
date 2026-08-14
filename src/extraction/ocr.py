"""PaddleOcrReader — the OCR fallback adapter, over PaddleOCR.

Implements `PdfTextPort` (`src/extraction/reader.py`) exactly like
`PypdfReader` does, so `FallbackPdfTextReader` can compose the two uniformly.
Renders each page to an image via `pypdfium2` (a pure wheel, no system
Poppler dependency — the same reason `pdf2image` was not chosen) and runs
PaddleOCR's detection+recognition pipeline over the rendered image.

**Lazy import, on purpose.** `paddleocr`/`paddlepaddle`/`pypdfium2` are an
optional dependency group (`pyproject.toml`'s `[project.optional-dependencies]
ocr`), not core — importing this MODULE must not require them installed;
only constructing a `PaddleOcrReader` does. Same idiom as
`src/bronze/scan.py`'s `ClamAVScanner`, which imports `socket` lazily inside
its own methods rather than at module load, so a deployment that never
configures the real adapter never pays the import cost or needs the package.

**Why this has no test exercised by default `make ci`.** `PaddleOCR()`
downloads model weights from a hoster on first construction — `make ci` runs
offline, so a test that constructs a real `PaddleOcrReader` cannot run there.
`tests/conformance/test_ocr_reader.py` is written and was run manually this
session against real weights (see its module docstring for the result); it
is marked to skip unless `INAFIN_RUN_OCR_TESTS=1` is set, the same
opt-in-by-env-var shape a real ClamAV integration test would need if this
repo had a live ClamAV container (`TODO.md`).
"""

from __future__ import annotations

import io
from typing import Any

from src.extraction.reader import NATIVE_TEXT_MIN_CHARS, PdfText


class PaddleOcrReader:
    """Renders PDF pages to images and OCRs them with PaddleOCR.

    One `PaddleOCR` pipeline instance is built at construction (loading/
    downloading model weights happens here, once) and reused across
    `extract()` calls — matching `PaddleOCR`'s own documented usage, which
    treats construction as the expensive step and `.predict()` as cheap.
    """

    def __init__(self, *, lang: str = "en") -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise ImportError(
                "PaddleOcrReader requires the optional 'ocr' dependency group"
                " (pip install '.[ocr]') — paddleocr/paddlepaddle are not"
                " core dependencies of this package"
            ) from exc
        self._ocr: Any = PaddleOCR(use_textline_orientation=False, lang=lang)

    def extract(self, data: bytes) -> PdfText:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(io.BytesIO(data))
        pages: list[str] = []
        try:
            for page in pdf:
                bitmap = page.render(scale=2.0)
                try:
                    image = bitmap.to_pil()
                finally:
                    bitmap.close()
                pages.append(self._extract_page_text(image))
        finally:
            pdf.close()

        total_chars = sum(len(page.strip()) for page in pages)
        return PdfText(
            pages=pages, has_native_text=total_chars >= NATIVE_TEXT_MIN_CHARS,
            source="ocr",
        )

    def _extract_page_text(self, image: object) -> str:
        import numpy as np

        results = self._ocr.predict(np.asarray(image))
        lines: list[str] = []
        for result in results:
            lines.extend(result.get("rec_texts") or [])
        return "\n".join(lines)
