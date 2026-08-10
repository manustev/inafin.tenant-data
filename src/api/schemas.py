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


class TriggerRequest(BaseModel):
    doc_type_code: str


class TriggerResponse(BaseModel):
    trigger_id: int
    ingest_id: uuid.UUID
    doc_type_code: str
    requested_at: dt.datetime
    status: str = "recorded"
    """Always "recorded" — no loader runs as a result of this call. See
    migrations/tenant/015_load_trigger.sql and TODO.md."""


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
