"""Document Type Registry gates.

The registry is shared reference data sitting inside tenant-db, which makes it
two things at once: a contract the CA team reviews, and a surface every tenant
role can reach. Both need gates.

  * **Integrity** — the register was resolved correctly and cannot drift from
    the reviewed CSV without a test failing.
  * **Isolation** — readable by every tenant, writable by none. A tenant role
    that could edit `document_type` could change another tenant's ingestion
    contract, since the table is shared.
"""

from __future__ import annotations

import csv
import pathlib

import psycopg
import pytest
from tests.conftest import SeededTenant

from src.core.errors import TenantBoundaryViolation
from src.core.pool import TenantScopedPool
from src.core.tenant import Role

pytestmark = pytest.mark.conformance

CSV_PATH = pathlib.Path(__file__).resolve().parents[2] / "registry" / "document_types.csv"

UMBRELLA_MARKER = "this ref is an alias"
ALIAS_MARKER = "canonical row is"


def _csv_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(CSV_PATH.open()))


# =============================================================================
# Integrity — the register was resolved correctly
# =============================================================================


def test_every_register_ref_resolves(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """No ref the CA team can quote is unresolvable.

    Every ref in Doc 4 Section 2 — canonical, alias, umbrella, and the two
    corpus-owned rows — must resolve to at least one document type. An
    unresolvable ref means an engagement checklist item with nothing behind it.
    """
    rows = _csv_rows()
    in_db = {
        r[0] for r in admin.execute(
            "SELECT DISTINCT register_ref FROM platform_ref.document_type_ref"
        ).fetchall()
    }
    missing = sorted({r["ref"] for r in rows} - in_db)
    assert not missing, f"register refs with no document type: {missing}"


def test_registry_matches_reviewed_csv(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The deployed registry equals the reviewed CSV.

    registry/document_types.csv is what a human signed off on. If someone edits
    the generated SQL by hand, or edits the CSV and forgets to regenerate, the
    reviewed artefact and the deployed data diverge silently. This is the check
    that makes the review mean something.

    table_name is included for a second reason. It is the one registry column
    scripts/gen_registry_seed.py does not emit — its values arrive in the shared
    migration that accompanies each batch of typed tables, because a regenerated
    seed would edit an applied migration (TYPED-TABLES-PLAN.md 3). This
    comparison is what stops the CSV and those hand-written migrations drifting.
    """
    expected = {
        r["doc_type_code"]: (
            r["obligation"], r["archetype"], r["silver_storage"], r["refresh_cadence"],
            r["table_name"],
        )
        for r in _csv_rows()
        if UMBRELLA_MARKER not in r["notes"] and ALIAS_MARKER not in r["notes"]
    }
    actual = {
        code: (
            obligation,
            "NONE" if archetype is None else str(archetype),
            storage,
            cadence or "",
            table_name or "",
        )
        for code, obligation, archetype, storage, cadence, table_name in admin.execute(
            "SELECT doc_type_code, obligation, archetype, silver_storage,"
            "       refresh_cadence, table_name"
            "  FROM platform_ref.document_type"
        ).fetchall()
    }
    assert actual == expected, "deployed registry has drifted from the reviewed CSV"


def test_exactly_one_canonical_ref_per_document_type(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The duplicate collapse holds.

    The register describes four documents twice (A1.20 = D1.01-07,
    B3.04 = D5.07, B3.05 = D5.06, C4.03 = D5.01). Two canonical refs for one
    code would mean two ingestion paths for one file and a completeness gate
    demanding it twice. The partial unique index enforces this; the test proves
    the index is doing the work.
    """
    orphans = admin.execute(
        "SELECT dt.doc_type_code FROM platform_ref.document_type dt"
        " WHERE NOT EXISTS ("
        "   SELECT 1 FROM platform_ref.document_type_ref r"
        "    WHERE r.doc_type_code = dt.doc_type_code AND r.is_canonical)"
    ).fetchall()
    assert not orphans, f"document types with no canonical ref: {orphans}"

    # The index makes a second canonical ref unrepresentable.
    with pytest.raises(psycopg.errors.UniqueViolation), admin.transaction():
        admin.execute(
            "INSERT INTO platform_ref.document_type_ref"
            " (register_ref, doc_type_code, is_canonical)"
            " SELECT 'X9.99', doc_type_code, TRUE"
            "   FROM platform_ref.document_type LIMIT 1"
        )


def test_in_scope_types_are_fully_specified(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Anything we will ingest has a stream, an archetype and a Silver decision.

    A document type in scope with a NULL archetype is a build with nowhere to
    put the data. The CHECK constraint forbids it; this proves no row slipped
    through before the constraint existed.
    """
    bad = admin.execute(
        "SELECT doc_type_code FROM platform_ref.document_type"
        " WHERE in_scope AND (stream IS NULL OR archetype IS NULL"
        "                     OR silver_storage = 'NONE')"
    ).fetchall()
    assert not bad, f"in-scope types missing an ingestion decision: {bad}"


def test_every_in_scope_type_has_a_refresh_cadence(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Operational mode needs to know when to re-request each document.

    Doc 4 Section 2: Mandatory documents are ingested at onboarding and
    refreshed at configured intervals. A type with no cadence is one the app
    layer can never decide to ask for again — it would be collected once at
    onboarding and then silently go stale.
    """
    missing = admin.execute(
        "SELECT doc_type_code FROM platform_ref.document_type"
        " WHERE in_scope AND refresh_cadence IS NULL"
    ).fetchall()
    assert not missing, f"in-scope types with no refresh cadence: {missing}"

    # And the constraint keeps it that way.
    with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
        admin.execute(
            "UPDATE platform_ref.document_type SET refresh_cadence = NULL"
            " WHERE in_scope"
        )


def test_corpus_owned_types_are_marked_out_of_scope(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """B4.03 and B4.04 are Mandatory but are not tenant data.

    Both are bitemporal threshold histories sourced from INAFIN Corpus. They
    are recorded rather than dropped so a Mandatory obligation cannot vanish —
    but they must never be counted as work for this repo.
    """
    rows = dict(
        admin.execute(
            "SELECT doc_type_code, in_scope FROM platform_ref.document_type"
            " WHERE doc_type_code IN ('EWB_THRESHOLD_HISTORY',"
            "                         'EINVOICE_THRESHOLD_HISTORY')"
        ).fetchall()
    )
    assert rows == {
        "EWB_THRESHOLD_HISTORY": False,
        "EINVOICE_THRESHOLD_HISTORY": False,
    }


def test_every_document_type_is_a_universal_master_value(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Bronze declares against universal_master, so the vocabularies must agree.

    A registry entry with no universal_master row is a document type that
    cannot be declared on intake — artefact_ledger's FK would reject it.
    """
    missing = admin.execute(
        "SELECT dt.doc_type_code FROM platform_ref.document_type dt"
        " WHERE NOT EXISTS ("
        "   SELECT 1 FROM platform_ref.universal_master um"
        "    WHERE um.value_type = 'Document_Type' AND um.value = dt.doc_type_code)"
    ).fetchall()
    assert not missing, f"document types not declarable on intake: {missing}"


# =============================================================================
# Isolation — shared reference data is read-only to tenants
# =============================================================================


async def test_tenant_roles_can_read_the_registry(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
) -> None:
    """Every tenant role reaches the registry through platform_ref_reader."""
    for role in (Role.INGEST, Role.RECON, Role.SUPPORT):
        async with app_pool.transaction(tenant_a.ctx, role) as conn:
            row = await (
                await conn.execute(
                    "SELECT count(*) FROM platform_ref.document_type WHERE in_scope"
                )
            ).fetchone()
            assert row is not None and row[0] == 125, f"{role} cannot read the registry"


async def test_tenant_roles_cannot_write_the_registry(
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
) -> None:
    """The registry is shared, so a tenant write would cross the boundary.

    A tenant that could UPDATE `document_type` would be editing every other
    tenant's ingestion contract — a cross-tenant mutation through a table that
    holds no tenant data at all.

    The refusal surfaces as TenantBoundaryViolation, not InsufficientPrivilege:
    TenantScopedPool maps every 42501 to it, so an isolation failure looks the
    same to callers whether it came from a schema grant or a shared table.
    """
    statements = (
        "INSERT INTO platform_ref.document_type"
        " (doc_type_code, name, category, grp, obligation, mode, sections,"
        "  stream, archetype, silver_storage, source_system, in_scope)"
        " VALUES ('EVIL', 'x', 'A', 'A1', 'CONDITIONAL', 'BOTH', ARRAY['A1']::text[],"
        "         'B', 1, 'STRUCTURED', 'x', TRUE)",
        "UPDATE platform_ref.document_type SET obligation = 'CONDITIONAL'",
        "DELETE FROM platform_ref.document_type",
        "UPDATE platform_ref.document_type_ref SET is_canonical = FALSE",
    )
    for role in (Role.INGEST, Role.RECON, Role.SUPPORT):
        for stmt in statements:
            with pytest.raises(TenantBoundaryViolation):
                async with app_pool.transaction(tenant_a.ctx, role) as conn:
                    await conn.execute(stmt)
