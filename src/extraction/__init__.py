"""PDF -> Silver extraction adapters (A2-A7 sample batch, 2026-08-11).

OVERVIEW. This package turns a PDF's native text layer into Silver rows for
the document types sampled in `reference/A1-A7Documents/`. It is tier 1 only —
deterministic `Label: Value` parsing — of the tiered design agreed in
`TODO.md` ("Archetype 3 extraction adapters"): deterministic first, an LLM
tier only on failure, never wired live this session.

    reader.py     PdfTextPort — bytes -> per-page text, and whether a native
                  text layer exists at all.
    labelvalue.py The shared `Label: Value` grammar. One parser, reused by
                  every archetype whose documents look like a header block of
                  facts (3, 6, 7, and the header portion of 4/8).
    base.py       DocumentExtractor ABC + one to_silver() hook per archetype
                  base class (EntitlementExtractor, ...). A concrete per-type
                  class supplies only a label spec — see `registry.py`.
    registry.py   EXTRACTOR_BY_DOC_TYPE — the one place a document type is
                  wired to its extractor.
    dispatch.py   run_extraction() — PDF bytes in, ExtractionOutcome out,
                  looked up by doc_type_code.

WHAT THIS IS NOT. Not the general Bronze->Silver dispatcher (`TODO.md`
priority 2) — it only resolves within the types this package builds
extractors for. Not an OCR pipeline — `PdfTextPort` has one real adapter and
a missing text layer always yields `NoTextLayer`. Not an LLM escalation tier —
a `Partial` outcome quarantines; it does not auto-retry.
"""
