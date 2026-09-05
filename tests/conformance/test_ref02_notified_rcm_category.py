"""platform_ref.v1_ref02_notified_rcm_category — shared migration 064.

inafin-reconciliation-engine's PRIM-07 request. Platform-wide, not
tenant-scoped — there is no entity_id/gstin column, unlike tenant_setting
(038) and gl_category_bridge (039), so cross-tenant reachability here is
CORRECT, not a leak: every tenant role (including both tenants'
recon_engine) is a member of platform_ref_reader (shared migration 001/060)
and reads the same category rows.

Two things this repo has never had before, both proven here rather than
just asserted: an EXCLUDE/gist temporal non-overlap constraint (contract
rule 3), and a platform_ref table granted ONLY through its v1_ view (every
other platform_ref table platform_ref_reader holds is a direct base-table
grant — this one is a deliberate exception).
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql

from src.core.identifiers import recon_engine_role

pytestmark = pytest.mark.conformance

_TEST_CATEGORY = "TEST_PRIM07_ISOLATION_CHECK"


@pytest.fixture
def _cleanup_test_category(admin: psycopg.Connection[tuple[object, ...]]):
    yield
    admin.execute(
        "DELETE FROM platform_ref.ref02_notified_rcm_category WHERE category_code = %s",
        (_TEST_CATEGORY,),
    )


def test_recon_engine_reads_the_view_both_tenants(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Platform data: BOTH tenants' recon_engine roles see the same rows —
    this is not a cross-tenant leak, it's the correct shape for law, not
    client config."""
    for slug in ("acme", "globex"):
        admin.execute(
            sql.SQL("SET ROLE {}").format(sql.Identifier(recon_engine_role(slug)))
        )
        admin.execute("SELECT count(*) FROM platform_ref.v1_ref02_notified_rcm_category")
        admin.execute("RESET ROLE")


def test_recon_engine_cannot_read_base_table(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Contract rule 5: base table never granted, only the v1_ view — a
    deliberate exception to platform_ref's own norm (hsn_master/
    universal_master are granted as base tables elsewhere)."""
    admin.execute(
        sql.SQL("SET ROLE {}").format(sql.Identifier(recon_engine_role("acme")))
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute("SELECT count(*) FROM platform_ref.ref02_notified_rcm_category")
    admin.execute("RESET ROLE")


def test_overlapping_approved_windows_rejected(
    admin: psycopg.Connection[tuple[object, ...]],
    _cleanup_test_category: None,
) -> None:
    """Contract rule 3: one category_code resolves to at most one APPROVED
    effective row for any date. Uses a clearly-synthetic TEST_ category code
    — this asserts the constraint mechanism, not real RCM law content."""
    admin.execute(
        "INSERT INTO platform_ref.ref02_notified_rcm_category "
        "(category_code, applicability_test, approval_state, effective_from, "
        "effective_to, reference_version, source_record_id, source_version) "
        "VALUES (%s, 'always_rcm', 'APPROVED', '2020-01-01', '2022-12-31', "
        "'TEST-v1', 'TEST-SRC-1', 'v1')",
        (_TEST_CATEGORY,),
    )
    with pytest.raises(psycopg.errors.ExclusionViolation):
        admin.execute(
            "INSERT INTO platform_ref.ref02_notified_rcm_category "
            "(category_code, applicability_test, approval_state, effective_from, "
            "effective_to, reference_version, source_record_id, source_version) "
            "VALUES (%s, 'always_rcm', 'APPROVED', '2021-06-01', '2023-06-01', "
            "'TEST-v1', 'TEST-SRC-2', 'v1')",
            (_TEST_CATEGORY,),
        )
