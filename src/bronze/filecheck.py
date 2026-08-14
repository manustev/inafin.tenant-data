"""File-shape validation for Bronze intake — before the scanner, before storage.

OVERVIEW. `check_file` runs three cheap, structural checks in order — not
empty, under the size cap, an allowed extension — and stops at the first
failure. It knows nothing about a document type's business content; that is
Silver's job (`src/silver/validate.py`, `src/silver/registers/loader.py`).
This module only decides whether the bytes are even worth scanning and
storing at all.

WHY THIS RUNS BEFORE THE VIRUS SCAN. A scan is comparatively expensive — a
network round trip to clamd or a cloud API (`src/bronze/scan.py`) — and
pointless on a file that is empty, absurdly large, or not a format Bronze
accepts. Running these checks first means a bad upload fails in microseconds
instead of after a scan round trip, and never reaches the object store either
way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Bronze's declared intake formats. Matches the register loader's two parsers
#: (src/silver/registers/loader.py: parse_register_csv, parse_register_ndjson)
#: plus plain "json" for a future array-of-records adapter — see TODO.md's
#: open item on JSON/Excel intake. "pdf" was added for the A2-A7 extraction
#: adapters (src/extraction/) — the 50 specimen documents in
#: reference/A1-A7Documents/ are all native-text PDFs. Extending this set is
#: how a new format is admitted at the door; it does not by itself mean
#: Silver can parse it yet.
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({"csv", "json", "ndjson", "pdf"})

#: 200 MiB. Comfortably above the largest A1 register export seen in mock
#: data (TODO.md's ~3.6M line rows/year, spread across weekly files, is nowhere
#: near this per file) — high enough to never reject a legitimate upload,
#: low enough to keep one malformed request from exhausting Bronze's storage
#: path before anyone reviews it.
DEFAULT_MAX_UPLOAD_BYTES: Final[int] = 200 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FileCheckResult:
    """The outcome of `check_file` — either the file may proceed, or `reason`
    says which rule stopped it, in the customer's own vocabulary (not a stack
    trace or an internal code) so it can be surfaced directly in an upload
    report."""

    ok: bool
    reason: str | None = None


def check_file(
    *,
    filename: str | None,
    data: bytes,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> FileCheckResult:
    """Run Bronze's structural gate over one upload.

    Checks run cheapest-first — an empty-body check costs nothing, so it runs
    before the size check, which runs before the extension check (a `str`
    comparison, still cheaper than anything touching `data` again). Stops at
    the first failure rather than collecting all of them: unlike Silver's
    row-level validation, there is nothing here for a customer to fix
    row-by-row — a file that fails one of these checks needs a different file,
    not a corrected one, so accumulating multiple reasons adds nothing.
    """
    if not data:
        return FileCheckResult(ok=False, reason="file is empty")

    if len(data) > max_bytes:
        return FileCheckResult(
            ok=False,
            reason=f"file is {len(data)} bytes, exceeding the {max_bytes}-byte limit",
        )

    extension = extension_of(filename)
    if extension not in allowed_extensions:
        return FileCheckResult(
            ok=False,
            reason=(
                f"file extension {extension!r} is not accepted"
                f" (expected one of {sorted(allowed_extensions)})"
            ),
        )

    return FileCheckResult(ok=True)


def extension_of(filename: str | None) -> str:
    """The lowercased suffix after the last '.', or '' for no filename / no dot.

    A caller with no filename at all (an API that accepts raw bytes with no
    multipart metadata) gets '' back, which is never in `ALLOWED_EXTENSIONS` —
    intake without a declared format is refused, not guessed at.
    """
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()
