"""REST request/response models.

Field names mirror the dataclasses they wrap (`ArtefactReceipt`,
`ArtefactOutcome`, `RowRejectionDetail`) rather than inventing a parallel
shape — the contract stays legible against `src/bronze/service.py` and
`src/reader/silver_reader.py` without a translation table.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel


class UploadResponse(BaseModel):
    ingest_id: uuid.UUID
    bucket: str
    object_key: str
    size_bytes: int
    deduplicated: bool


class BatchUploadItem(BaseModel):
    """One file's outcome within a batch upload. `ok` is the field a caller
    branches on; `error` mirrors `IntakeRejected`'s message verbatim (same
    text the single-file route returns as its 422 `detail`) so a batch and a
    single upload report an identical reason for an identical failure."""

    filename: str | None
    ok: bool
    ingest_id: uuid.UUID | None = None
    bucket: str | None = None
    object_key: str | None = None
    size_bytes: int | None = None
    deduplicated: bool | None = None
    error: str | None = None


class BatchUploadResponse(BaseModel):
    """Always 200, never 201 — a batch's own HTTP status cannot represent a
    mix of accepted and refused files, so the caller must read `items`. Same
    "the body carries the outcome, not the status line" shape
    `StatusResponse.status`'s PARTIAL/QUARANTINED vocabulary already uses."""

    accepted_count: int
    rejected_count: int
    items: list[BatchUploadItem]


class TriggerRequest(BaseModel):
    doc_type_code: str
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    gstin: str | None = None
    """Only required for the SALES_REGISTER/REGISTER_LOADER/ARCHETYPE1_PROMOTE
    dispatch mechanisms (src/dispatch/router.py) — a PDF-shaped type
    (PDF_EXTRACTION) needs none of these, since a PDF is a single dated
    instrument, not a period export. Omitting a field a mechanism does need
    is a 422, not a silent no-op."""


class TriggerResponse(BaseModel):
    trigger_id: int
    ingest_id: uuid.UUID
    doc_type_code: str
    requested_at: dt.datetime
    status: str
    """One of SilverReader.artefact_outcome's vocabulary
    (ACCEPTED/PARTIAL/QUARANTINED) once dispatch_load has actually run, or
    UNROUTED if doc_type_code has no dispatch_mechanism yet."""
    mechanism: str | None = None
    """Which of the four mechanisms in src/dispatch/router.py handled this
    trigger, or None for UNROUTED."""
    batch_id: uuid.UUID | None = None


class RowRejectionDetail(BaseModel):
    source_line: int
    column_name: str | None
    message: str


class StatusResponse(BaseModel):
    bronze_ingest_id: uuid.UUID
    status: str
    document_type: str | None
    batch_id: uuid.UUID | None
    row_count: int | None
    accepted_count: int | None
    rejected_count: int
    quarantine_reason: str | None
    rejections: list[RowRejectionDetail]
