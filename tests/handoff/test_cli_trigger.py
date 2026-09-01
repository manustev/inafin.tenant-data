"""`tenantctl trigger` — the manual local trigger, run as a REAL subprocess
against the live shared cluster, not by calling `cmd_trigger` in-process.

WHY A SUBPROCESS. `cmd_trigger` builds its own pool, object store, scanner and
Kafka publisher from `get_settings()` and tears them down again
(`src/cli.py`'s `_run_trigger`) — that lifecycle, and the argparse wiring in
front of it, is exactly what a direct function call would skip. Every other
`tenantctl` command in this repo (`migrate`, `provision`, `drift`) has been
verified by hand each session rather than by an automated test; this one gets
one because it is new, user-facing, and the whole reason it exists is to
behave identically to `POST /artefacts/{id}/trigger` — worth a gate, not just
a session note.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from psycopg import sql
from tests.conftest import SeededTenant

from src.bronze.service import BronzeIngestionService
from src.core.pool import TenantScopedPool
from src.silver.registers import spec_for

pytestmark = pytest.mark.conformance

ROOT = pathlib.Path(__file__).resolve().parents[2]
GSTIN = "27AAFCI9876P1ZQ"
PURCHASE_REGISTER_SPEC = spec_for("PURCHASE_REGISTER")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "src.cli", "trigger", *args],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )


async def test_cli_trigger_dispatches_a_real_upload(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC, seed="A", overrides={"invoice_no": "PI-CLI-OK"}
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="PURCHASE_REGISTER",
    )

    result = _run_cli(
        tenant_a.slug, str(receipt.ingest_id), "PURCHASE_REGISTER",
        "--period-start", "2026-04-01", "--period-end", "2026-04-30",
        "--gstin", GSTIN,
    )
    assert result.returncode == 0, result.stderr
    assert "ACCEPTED" in result.stdout
    assert "REGISTER_LOADER" in result.stdout


def test_cli_trigger_on_an_unknown_ingest_id_exits_nonzero(
    tenant_a: SeededTenant,
) -> None:
    result = _run_cli(tenant_a.slug, str(uuid.uuid4()), "PURCHASE_REGISTER")
    assert result.returncode == 1
    assert "no artefact" in result.stderr


async def test_cli_trigger_missing_a_required_field_exits_nonzero(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC, seed="A", overrides={"invoice_no": "PI-CLI-MISSING"}
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="PURCHASE_REGISTER",
    )

    result = _run_cli(tenant_a.slug, str(receipt.ingest_id), "PURCHASE_REGISTER")
    assert result.returncode == 1
    assert "missing required field" in result.stderr


async def test_cli_trigger_matches_the_http_route_exactly(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Both callers go through `record_and_dispatch_trigger` — this proves it
    at the seam that matters: the SAME artefact, triggered once through the
    CLI, produces the SAME kind of `ingest_batch` row an HTTP trigger would.
    Not a duplicate of `test_api_ingest.py`'s own trigger tests; this is the
    CLI's half of that same guarantee."""
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC, seed="A", overrides={"invoice_no": "PI-CLI-PARITY"}
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="t.csv",
        document_type="PURCHASE_REGISTER",
    )

    result = _run_cli(
        tenant_a.slug, str(receipt.ingest_id), "PURCHASE_REGISTER",
        "--period-start", "2026-04-01", "--period-end", "2026-04-30",
        "--gstin", GSTIN,
    )
    assert result.returncode == 0, result.stderr
    assert "batch=" in result.stdout
    batch_id = result.stdout.strip().rsplit("batch=", 1)[1]

    row = admin.execute(
        sql.SQL(
            "SELECT document_type, row_count FROM {}.ingest_batch WHERE batch_id = %s"
        ).format(sql.Identifier(tenant_a.ctx.silver_schema)),
        (batch_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "PURCHASE_REGISTER"
    assert row[1] == 1


async def test_cli_trigger_reports_a_constraint_violation_as_one_clean_line(
    bronze: BronzeIngestionService,
    app_pool: TenantScopedPool,
    tenant_a: SeededTenant,
    entity_id: uuid.UUID,
) -> None:
    """The CLI's half of the 2026-09-01 error-mapping fix.

    There is only one exit code to give (1 either way), so the CONFLICT /
    INVALID distinction the HTTP route carries as 409-versus-422 has to live
    in the text here — printed explicitly rather than left for an operator to
    infer from the Postgres message. The constraint name is the fastest route
    to which check actually fired.

    A traceback would also be "an error", which is why this asserts the
    absence of one: an unhandled `psycopg` exception out of `asyncio.run` is
    what this replaced, and it is indistinguishable to a caller from the CLI
    itself being broken.
    """
    from tests.conformance.test_registers import synthetic_csv

    data = synthetic_csv(
        PURCHASE_REGISTER_SPEC,
        seed="A",
        overrides={
            "supplier_gstin": "NOTAGSTIN12345",
            "invoice_no": f"PI-CLI-BADGSTIN-{uuid.uuid4().hex[:8]}",
        },
    )
    receipt = await bronze.receive(
        tenant_a.ctx, entity_id=entity_id, data=data, filename="bad_gstin.csv",
        document_type="PURCHASE_REGISTER",
    )

    result = _run_cli(
        tenant_a.ctx.slug, str(receipt.ingest_id), "PURCHASE_REGISTER",
        "--period-start", "2026-04-01", "--period-end", "2026-04-30",
        "--gstin", GSTIN,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("ERROR: INVALID:")
    assert "gstin_check" in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1
