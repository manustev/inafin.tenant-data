"""Infer a `dispatch_load(content_format=...)` value from a filename.

Reuses `src/bronze/filecheck.py`'s extension vocabulary rather than
reinventing one — `ALLOWED_EXTENSIONS` is what Bronze already accepted at
upload time, so this can only ever see one of those four suffixes.
"""

from __future__ import annotations

from src.bronze.filecheck import extension_of

#: "json" is accepted at intake as NDJSON (CLAUDE.md: "JSON resolved to
#: NDJSON" — the array-of-records case TODO.md once flagged as unbuilt is
#: still unbuilt; a plain ".json" upload is treated as newline-delimited).
_FORMAT_BY_EXTENSION: dict[str, str] = {
    "csv": "csv",
    "ndjson": "ndjson",
    "json": "ndjson",
    "pdf": "pdf",
}


def infer_content_format(filename: str | None) -> str:
    """Raises `ValueError` for an extension `check_file` would have already
    refused at upload time — reachable only if a caller bypasses upload
    (a test, a batch replay), never through the API."""
    extension = extension_of(filename)
    try:
        return _FORMAT_BY_EXTENSION[extension]
    except KeyError:
        raise ValueError(
            f"cannot infer a dispatch content_format from filename {filename!r}"
            f" (extension {extension!r})"
        ) from None
