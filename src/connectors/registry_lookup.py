"""`resolve_document_source` — the ref -> source-system / consuming-sections /
mode lookup, read from data that already exists.

`reference/B-Document_prompt.md` (this session's brief) asked for "a mapping
table in DB where we will store B1.01 will be used by A1,A6,A7,B6 and its
applicable to both Forensic and Regular." That table already exists, split
across two: `platform_ref.document_type.sections` (a native `text[]`) and
`.mode`, populated for every Category B row from
`INAFIN_Recon_Doc4_Sec2_SourceDocRegister_v2..md`'s own columns (shared
migration `003`, unchanged by this session — verified directly against the
live cluster, `GSTR_1`'s `sections` reads `{A1,A2,A7,A8,A9,A10}` and `mode`
reads `BOTH`, an exact match to the source register's row); and
`platform_ref.document_type_ref`, which is the `ref` (`"B1.01"`) <->
`doc_type_code` (`"GSTR_1"`) mapping itself — a separate table, not a column
on `document_type`, because a ref can resolve to more than one code (`003`'s
own comment: `A1.20` resolves to seven). `source_system`
(`GSTN_API`/`GSTN_PORTAL`/`ICEGATE_API`/`DGFT_PORTAL`/`IRP_API`/
`EWB_PORTAL_API`/`COURT_REGISTRY`/`INAFIN_CORPUS`) is the same column
`factory.py` keys its connector dispatch on. This module is a thin, typed
read joining those two tables — not a new one — so nothing here duplicates
data the registry already carries correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from src.core.pool import TenantScopedPool
from src.core.tenant import Role, TenantContext


@dataclass(frozen=True, slots=True)
class DocumentSourceMeta:
    """One `platform_ref.document_type` row's connector-relevant columns.

    Attributes:
        ref: The registry's own primary key (`"B1.03"`) — what
            `LocalFixtureConnector` and the staged fixture tree are keyed by.
        doc_type_code: The stable code (`"GSTR_2B"`) `dispatch_load` and
            every Silver table already key on.
        source_system: Which connector answers this ref — `factory.py`'s
            dispatch key.
        sections: The reconciliation sections this document feeds, exactly
            as the source document register names them (e.g.
            `("A1", "A2", "A7", "A8", "A9", "A10")` for `GSTR_1`) — this is
            the "B1.01 is used by ..." mapping the brief asked for.
        mode: `"FORENSIC" | "OPERATIONAL" | "BOTH"` — which engagement
            mode(s) require this document.
    """

    ref: str
    doc_type_code: str
    source_system: str
    sections: tuple[str, ...]
    mode: str


class UnknownDocumentTypeError(ValueError):
    """`doc_type_code` is not an in-scope row in `platform_ref.document_type`
    — distinct from `ConnectorNotConfiguredError`/`SourceDocumentNotFoundError`,
    which mean the registry lookup succeeded but the fetch itself could not."""


async def resolve_document_source(
    pool: TenantScopedPool, ctx: TenantContext, doc_type_code: str,
) -> DocumentSourceMeta:
    """Same read idiom `dispatch/router.py::_resolve_mechanism` and
    `silver/promote.py::_load_spec` already use for other per-type registry
    columns — read per call, not cached, so a corrected `source_system` or
    `sections` cell takes effect on the next connector call without a
    process restart.

    Joins on `document_type_ref.is_canonical` so a `doc_type_code` reachable
    from more than one ref (the `A1.20` case `003`'s own schema comment
    names) still resolves to exactly one `DocumentSourceMeta` — the ref a
    fixture folder would actually be staged under."""
    async with pool.transaction(ctx, Role.INGEST) as conn:
        row = await (
            await conn.execute(
                "SELECT r.register_ref, t.source_system, t.sections, t.mode"
                " FROM platform_ref.document_type t"
                " JOIN platform_ref.document_type_ref r"
                "   ON r.doc_type_code = t.doc_type_code AND r.is_canonical"
                " WHERE t.doc_type_code = %s AND t.in_scope",
                (doc_type_code,),
            )
        ).fetchone()
    if row is None:
        raise UnknownDocumentTypeError(
            f"{doc_type_code!r} is not an in-scope document type in the registry"
        )
    ref, source_system, sections, mode = cast(
        "tuple[str, str, list[str], str]", row
    )
    return DocumentSourceMeta(
        ref=ref, doc_type_code=doc_type_code, source_system=source_system,
        sections=tuple(sections), mode=mode,
    )
