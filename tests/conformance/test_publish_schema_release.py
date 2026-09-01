"""The publisher's exclusion list — the one rule in it that is a correctness
rule rather than tidiness.

`reference/B-Documents/` ships `ANOMALY_KEY.json`, the expected-results ORACLE
for the Category B mock set: it names all 19 deliberately seeded anomalies.
Those anomalies exist so that reconciliation logic can be tested against
documents that are subtly wrong. Publishing the key to the tenants whose data
that logic is meant to check would hand over the answer sheet.

Nothing about the directory layout prevents this on its own — the file sits at
the sample set's root today, but a regenerated sample set could put it
anywhere. `_EXCLUDED_FILENAMES` is the actual defence, so it gets a test.
"""

from __future__ import annotations

import json
import pathlib
import sys

import psycopg
import pytest

pytestmark = pytest.mark.conformance

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import publish_schema_release as pub  # noqa: E402


def test_the_anomaly_oracle_is_excluded_by_name() -> None:
    assert "ANOMALY_KEY.json" in pub._EXCLUDED_FILENAMES


def test_no_excluded_file_is_ever_yielded_for_publication() -> None:
    """Against the REAL reference tree, not a fixture.

    A glob-based exclusion, or one that depended on the oracle staying at the
    sample set's root, would pass a fixture test and fail here the day someone
    regenerates the samples.
    """
    published = [path.name for _ref, path in pub._sample_files()]
    assert published, "fixture assumption: the reference sample sets exist"

    leaked = sorted(set(published) & pub._EXCLUDED_FILENAMES)
    assert leaked == [], f"excluded files reached the publisher: {leaked}"


def test_the_oracle_would_be_caught_even_inside_a_ref_folder(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exclusion must be by NAME, not by where the file happens to sit.

    Today the oracle lives at the sample set's root, which `_sample_files`
    never walks into — so the previous test would still pass if the exclusion
    list were empty. This one puts the oracle exactly where a careless
    regeneration would put it (inside a ref folder, alongside real documents)
    and proves the name check is what stops it.
    """
    group = tmp_path / "B1_GSTN_Filed_Returns" / "B1.03_GSTR-2B"
    group.mkdir(parents=True)
    (group / "GSTR2B_29AABCV1234K1Z9_112024.json").write_bytes(b"{}")
    (group / "ANOMALY_KEY.json").write_bytes(b'{"MOCK-01": "seeded"}')

    monkeypatch.setattr(pub, "B_DOCUMENTS", tmp_path)
    monkeypatch.setattr(pub, "A_DOCUMENTS", tmp_path / "does-not-exist")

    names = [path.name for _ref, path in pub._sample_files()]
    assert names == ["GSTR2B_29AABCV1234K1Z9_112024.json"]


def test_render_schema_carries_the_constraint_block_where_the_db_has_one(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The renderer half of the 2026-09-01 fix (migration 041/042).

    `_render_schema` previously projected only `ordinal, field_name, scope,
    data_type, required, source_label` — so a v2 release, before this, would
    have been byte-identical to v1 except for the `release` string, and the
    constraint columns added to `document_type_field` would have reached no
    published file at all. This is the piece that actually surfaces them.
    """
    out = pub._render_schema(admin, "v2")
    body = json.loads(out["PURCHASE_REGISTER"])
    by_name = {f["name"]: f for f in body["fields"]}

    assert by_name["supplier_gstin"]["constraints"] == {
        "sql_domain": "platform_ref.gstin",
        "pattern": "^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$",
    }
    assert by_name["gst_rate"]["constraints"]["min_value"] == "0"
    assert by_name["gst_rate"]["constraints"]["max_value"] == "100"
    assert by_name["itc_eligibility"]["constraints"]["allowed_values"] == [
        "ELIGIBLE", "INELIGIBLE", "BLOCKED", "PARTIAL",
    ]
    # invoice_no carries a required flag and NOTHING else — no domain, no
    # vocabulary, no bound — so the key must be absent, not present-and-empty.
    assert "constraints" not in by_name["invoice_no"]


def test_render_schema_surfaces_multi_column_rules(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The one constraint that belongs to no single field.

    `sales_register_line`'s intra/inter-state rule (IGST positive implies
    CGST and SGST zero) is real and a client can trip over it; it must reach
    the published file as `rules`, not silently vanish because it isn't one
    field's problem.
    """
    out = pub._render_schema(admin, "v2")
    body = json.loads(out["SALES_REGISTER"])
    assert body["rules"] == [{
        "constraint_name": "sales_register_line_tax_head",
        "expression": (
            "((COALESCE((igst), (0)) > (0)) AND (COALESCE((cgst), (0)) = (0)) "
            "AND (COALESCE((sgst), (0)) = (0))) OR (COALESCE((igst), (0)) = (0))"
        ),
        "columns": ["igst", "cgst", "sgst"],
    }]


def test_render_schema_publishes_no_constraints_for_a_declared_type(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A DECLARED type's field list comes from a registry grammar cell with no
    table behind it — there is nothing in `pg_constraint` to read. The key
    must be absent everywhere on that type, not present-and-null: `null`
    would read as "the database accepts anything" when the truth is "we do
    not know". `provenance` in the same body already tells a client which
    case they are in.
    """
    out = pub._render_schema(admin, "v2")
    declared_code = admin.execute(
        "SELECT doc_type_code FROM platform_ref.document_type_schema"
        " WHERE provenance = 'DECLARED' LIMIT 1"
    ).fetchone()
    assert declared_code is not None
    body = json.loads(out[str(declared_code[0])])
    assert body["provenance"] == "DECLARED"
    assert not any("constraints" in f for f in body["fields"])
    assert "rules" not in body


def test_render_schema_is_byte_stable_across_two_calls(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The sha256 in `schema_artifact` is only a meaningful comparison —
    "did this file actually change between releases" — if an unchanged
    catalogue renders identically every time. Key order and array order
    both matter to that, not just content."""
    first = pub._render_schema(admin, "v2")
    second = pub._render_schema(admin, "v2")
    assert first == second
