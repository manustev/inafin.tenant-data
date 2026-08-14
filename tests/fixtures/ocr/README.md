# OCR fixtures

`lut_scanned_synthetic.pdf` is a **synthetic scan**, not a real scanned
document — no genuinely scanned PDF exists anywhere in this workspace
(`HANDOFF-2026-08-11.md`). It was built by rendering page 1 of the real
specimen `reference/A1-A7Documents/A5.01_LUT_Letter_of_Undertaking.pdf` to a
raster image (`pypdfium2`, 2x scale) and re-saving that image alone as a
fresh single-page PDF (Pillow's PDF writer embeds only the raster — no text
layer, confirmed via `PypdfReader().extract(...).has_native_text is False`).

This is legitimate, not a "guessed layout, no ground truth" fixture the way
a hand-typed OCR sample would be: the ground truth is the SAME specimen
`test_extraction_entitlement.py` already extracts via the native-text path
and asserts `instrument_number == "AD2704240123456"` for. `test_ocr_reader
.py` asserts the OCR path recovers the same value from the pixels alone.

Do not regenerate this file to "improve" it — if a real scanned document
ever becomes available, that should be a NEW fixture, not a replacement for
this one, so the synthetic-vs-real provenance stays visible.
