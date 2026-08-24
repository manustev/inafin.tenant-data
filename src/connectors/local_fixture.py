"""`LocalFixtureConnector` — the folder-backed stand-in for every live source.

Answers `SourceConnectorPort.fetch()` from a tree structured

    <fixture_root>/<tenant_slug>/<ref>/<filename>

rather than an HTTP call, because no source system's real credentials exist
in this workspace (`Settings.source_data_mode` defaults to
`"local_fixture"`). `scripts/stage_bronze_fixtures.py` populates that tree
from `reference/B-Documents/inafin_mock_categoryB/` — see that script's
docstring for the one-time staging step this connector depends on.

FILE RESOLUTION. A ref's folder can hold several files at once — a periodic
return ships one file per (GSTIN, period) plus a flat all-periods CSV
(`GSTR2B_all_lines_FY2024-25.csv`); a register-shaped ref ships one file,
sometimes in more than one format (`open_scn_register.csv` **and** `.json`
**and** a PDF specimen). Two resolution rules, both driven by what the
2026-08-19 sample-data comparison actually found (see
`HANDOFF-2026-08-19-categoryB.md` and this session's connector-layer prompt,
`reference/B-Document_prompt.md`):

1. When `gstin`/`period` are given, only filenames containing BOTH survive —
   which naturally excludes the flat all-periods rollups (they carry no
   single GSTIN) without this connector needing to know their filenames.
2. When more than one format remains, prefer `json` over `csv` over `pdf`.
   JSON is source of record for the nested returns (GSTR-1/2B/3B/9/9C all
   drop real structure — supplier grouping, ITC-availability flags, section
   detail — when flattened to their sibling CSV, confirmed by direct key
   comparison, not assumed); for register-shaped refs where CSV and JSON
   carry the same fields this preference is merely a tie-break, not a
   correctness requirement. PDFs are never preferred: the three PDF
   specimens in the sample set are explicitly flagged as cosmetic stand-ins,
   not real portal layouts, in `reference/B-Documents/.../README.md` §5.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from src.bronze.filecheck import extension_of
from src.connectors.base import FetchedDocument, SourceDocumentNotFoundError

#: Preference order when more than one format matches — see module
#: docstring rule 2. A format not in this tuple is never returned.
_FORMAT_PREFERENCE: tuple[str, ...] = ("json", "csv", "pdf")


class LocalFixtureConnector:
    """Implements `SourceConnectorPort` by reading `fixture_root` instead of
    calling out to a live source. One instance is shared across every
    `source_system` in local-fixture mode — unlike the live adapters, there
    is nothing per-source-system to configure."""

    def __init__(self, *, fixture_root: Path) -> None:
        self._root = fixture_root

    async def fetch(
        self,
        *,
        tenant_slug: str,
        doc_type_code: str,  # noqa: ARG002 — part of SourceConnectorPort, unused here
        ref: str,
        gstin: str | None = None,
        period: str | None = None,
    ) -> FetchedDocument:
        """`doc_type_code` is accepted (required by `SourceConnectorPort`)
        but not used for resolution — the fixture tree is organised by `ref`
        alone, since that is what `scripts/stage_bronze_fixtures.py` staged
        it by and what a ref folder can unambiguously be checked out for.
        """
        ref_dir = self._root / tenant_slug / ref
        if not ref_dir.is_dir():
            raise SourceDocumentNotFoundError(
                f"no fixture folder for tenant={tenant_slug!r} ref={ref!r} "
                f"under {self._root} — has scripts/stage_bronze_fixtures.py "
                f"been run for this tenant?"
            )

        candidates = [
            p for p in sorted(ref_dir.iterdir())
            if p.is_file() and extension_of(p.name) in _FORMAT_PREFERENCE
        ]
        if gstin is not None:
            candidates = [p for p in candidates if gstin in p.name]
        if period is not None:
            candidates = [p for p in candidates if period in p.name]

        chosen = _pick_preferred(candidates)
        if chosen is None:
            raise SourceDocumentNotFoundError(
                f"no fixture file for tenant={tenant_slug!r} ref={ref!r} "
                f"gstin={gstin!r} period={period!r} under {ref_dir}"
            )

        return FetchedDocument(
            content=chosen.read_bytes(),
            filename=chosen.name,
            content_format=extension_of(chosen.name),
            source_ref=str(chosen.relative_to(self._root)),
            fetched_at=dt.datetime.now(dt.UTC),
        )


def _pick_preferred(candidates: list[Path]) -> Path | None:
    """`_FORMAT_PREFERENCE` order, first match wins. Ties within one format
    (e.g. two stray `.json` files) resolve to the alphabetically-first path,
    since `candidates` is already `sorted()` by the caller and this keeps
    the first one seen per format."""
    by_format: dict[str, Path] = {}
    for p in candidates:
        by_format.setdefault(extension_of(p.name), p)
    for fmt in _FORMAT_PREFERENCE:
        if fmt in by_format:
            return by_format[fmt]
    return None
