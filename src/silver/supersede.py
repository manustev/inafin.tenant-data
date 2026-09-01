"""Find and close the current version of a row, so a correction can replace it.

WHAT WAS ACTUALLY MISSING. Three of the five PDF archetypes
(`entitlement.py`, `proceeding_event.py`, `entity_master.py`) have carried a
working `supersede(prior_id=..., rec=...)` since they were written, and all
five tables have a `supersedes_*` column and a partial `*_current_uq` index —
the schema was designed for this from the start. What no caller ever had was
the LOOKUP: nothing answered "which row is the current one for this
document", so `src/extraction/base.py` could only ever call `record()`, a
blind INSERT. The second ingest of any document therefore had to violate the
unique index. Reported by the ERP upload E2E suite (2026-09-01) as
`entitlement_instrument_current_uq`, `narrative_contract_current_uq` and
`financial_statement_extract_current_uq` failures, and misread at first as
the reference corpus reusing PDFs across document types — it does not
(50 specimens, 50 distinct hashes, one per PDF_EXTRACTION type).

THE KEY IS THE INDEX'S COLUMNS, NOT "the business key" in the abstract. Callers
pass exactly the columns of the table's partial unique index, in its order, for
the same reason `RegisterSpec.key_columns` is defined that way: a lookup that
disagrees with the index does not prevent a duplicate, it just moves where the
duplicate appears. The two key strategies `src/silver/registers/spec.py`
documents both occur here and both work through this one function —

    NATURAL   entitlement_instrument (entity_id, instrument_type,
              instrument_number), proceeding_event and entity_master_record on
              their reference numbers. A corrected document carrying the same
              identifier supersedes its predecessor. This is correction.

    CONTENT   financial_statement_extract and narrative_contract, keyed on
              (entity_id, type, bronze_ingest_id) because no reference number
              exists on the document to key on. The artefact IS the identity,
              so a collision means the SAME artefact was dispatched twice and
              superseding records a RE-EXTRACTION of it — the prior reading
              closed, the new one current. Migration 018's header calls this
              index a "resubmission guard"; it still guards, but a
              resubmission is now handled rather than refused.

WHY `FOR UPDATE`. Without it two concurrent triggers for the same document
both see no current row, both insert, and one loses on the index — turning a
correction into an error under exactly the concurrent fan-out this codebase
is otherwise careful about. The lock is taken on the row that is about to be
closed, and the whole find-close-insert sequence runs in ONE transaction:
committed separately, a reader between them would see either two current
rows (which the index refuses) or none at all, and "none" would momentarily
withdraw a fact the tenant holds.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import cast

import psycopg
from psycopg import sql


async def close_current(
    conn: psycopg.AsyncConnection[tuple[object, ...]],
    *,
    schema: str,
    table: str,
    id_column: str,
    key: Mapping[str, object],
) -> uuid.UUID | None:
    """Close the current row matching `key` and return its id, or None.

    None means there is nothing to supersede and the caller should insert a
    first version — not that the lookup failed. The caller must run this and
    its INSERT in the same transaction; this function deliberately does not
    open one, so it cannot be used in a way that splits them.
    """
    where = sql.SQL(" AND ").join(
        sql.SQL("{} = %s").format(sql.Identifier(column)) for column in key
    )
    row = await (
        await conn.execute(
            sql.SQL(
                "SELECT {id} FROM {schema}.{table}"
                " WHERE {where} AND superseded_at IS NULL"
                " FOR UPDATE"
            ).format(
                id=sql.Identifier(id_column),
                schema=sql.Identifier(schema),
                table=sql.Identifier(table),
                where=where,
            ),
            tuple(key.values()),
        )
    ).fetchone()

    if row is None:
        return None

    prior_id = cast(uuid.UUID, row[0])
    await conn.execute(
        sql.SQL(
            "UPDATE {schema}.{table} SET superseded_at = now()"
            " WHERE {id} = %s AND superseded_at IS NULL"
        ).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(table),
            id=sql.Identifier(id_column),
        ),
        (prior_id,),
    )
    return prior_id
