"""Generic multi-row writer for a table-shaped archetype-7 type's facts.

`SHAREHOLDING_PATTERN`'s `shareholding_pattern_holder` (tenant migration 032)
is the first table this writes to; every future table-shaped archetype-7
type (`GSTIN_REGISTER`, `DIRECTOR_LIST_WITH_DIN`, `RELATED_PARTY_REGISTER`,
`KMP_LIST`) gets its OWN typed table with its own business columns, but all
of them share the same envelope (`fact_id`, `record_id`, `entity_id`,
`bronze_ingest_id`, `extraction_run_id`, `batch_id`, `superseded_at`,
`supersedes_fact_id`) and the same write shape — this module is that shared
shape, parameterized on table/column names, so adding the Nth table-shaped
type does not mean a Nth copy of this INSERT.

**Supersede is full-snapshot replace, not per-row diffing** — a deliberate
simplification for this phase (see tenant migration 032's header): every
currently-open fact row under the PRIOR `record_id` is closed, and the new
PDF's complete row set is inserted fresh under the NEW `record_id`.
`supersedes_fact_id` is left `NULL` here on purpose — row-to-row lineage
across a resubmission would need a per-type natural key (which columns
identify "the same row" across two documents), which varies by type and is
not needed for this table to already be useful: `record_id` itself chains
through `entity_master_record.supersedes_record_id`, so a fact's full
snapshot lineage is traceable, just not at the individual-row grain yet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg
from psycopg import sql


@dataclass(frozen=True, slots=True)
class FactRowEnvelope:
    """The columns every table-shaped archetype-7 fact table shares, on top
    of whichever business columns that ONE type's rows carry."""

    entity_id: uuid.UUID
    record_id: uuid.UUID
    batch_id: uuid.UUID
    bronze_ingest_id: uuid.UUID
    extraction_run_id: uuid.UUID


async def close_prior_fact_rows(
    conn: psycopg.AsyncConnection[tuple[object, ...]],
    *,
    schema: str,
    table: str,
    prior_record_id: uuid.UUID,
) -> None:
    """Close every currently-open fact row under a superseded header record.
    A no-op, correctly, if `prior_record_id` is the first snapshot (nothing
    to close) — callers pass `None` for that case and never call this."""
    await conn.execute(
        sql.SQL(
            "UPDATE {}.{} SET superseded_at = now()"
            " WHERE record_id = %s AND superseded_at IS NULL"
        ).format(sql.Identifier(schema), sql.Identifier(table)),
        (prior_record_id,),
    )


async def record_fact_rows(
    conn: psycopg.AsyncConnection[tuple[object, ...]],
    *,
    schema: str,
    table: str,
    envelope: FactRowEnvelope,
    rows: list[dict[str, object]],
) -> None:
    """Insert one fact row per `rows` entry, all under the SAME new
    `record_id` — the whole point being that a header write and its N line
    writes commit in one transaction, same discipline every other
    archetype's header+line write already follows
    (`promote_transaction_documents`, `sales_register.py`).

    `rows` entries are plain `{column_name: coerced_value}` dicts — exactly
    what `tablevalue.parse_table_rows` (or a type-specific expansion on top
    of it, e.g. `SHAREHOLDING_PATTERN`'s one-PDF-row-to-two-facts FY split)
    produces. This function does not know or care what the business columns
    ARE, only that every row in `rows` names the same set of them.
    """
    for row in rows:
        columns = [
            "fact_id", "record_id", "entity_id",
            *row.keys(),
            "bronze_ingest_id", "extraction_run_id", "batch_id",
        ]
        values: list[object] = [
            uuid.uuid4(), envelope.record_id, envelope.entity_id,
            *row.values(),
            envelope.bronze_ingest_id, envelope.extraction_run_id, envelope.batch_id,
        ]
        await conn.execute(
            sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                sql.Identifier(schema),
                sql.Identifier(table),
                sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            ),
            values,
        )


__all__ = ["FactRowEnvelope", "close_prior_fact_rows", "record_fact_rows"]
