"""Publish a schema release: the field schemas and sample documents a tenant
downloads before they ever upload anything.

WHAT IT PUBLISHES, per release (`v1`, `v2`, ...):

  SCHEMA  one JSON file per in-scope document type, rendered from
          `platform_ref.document_type_schema` / `document_type_field` — the
          catalogue seeded by shared migration 036 and gated by
          `tests/conformance/test_schema_catalogue.py`. Nothing is invented
          here; this script is a renderer, not a second source of truth.
  SAMPLE  the example documents in `reference/`, mapped to their document type
          through `platform_ref.document_type_ref`.

The bytes go to the platform bucket (one bucket, same files for every tenant —
see `objectstore.py`'s note on why this one is not per-tenant), and the
manifest rows to `platform_ref.schema_artifact`.

TWO FILES ARE DELIBERATELY NEVER PUBLISHED, and this is the important part of
this script:

  * `ANOMALY_KEY.json` — the expected-results ORACLE for the Category B mock
    set. It names all 19 seeded anomalies. Publishing it to the tenants whose
    data those anomalies are meant to catch would hand them the answer sheet.
  * `MANIFEST.json` / `README.md` at the sample-set root, and anything under
    `_generator/` — sample-set bookkeeping and the generator's own source, not
    documents of any type.

`_EXCLUDED_FILENAMES` is that list, and `test_publish_schema_release.py` pins
it. Do not "simplify" the exclusion into a glob that a new file could slip
past.

STATUS. A release is created DRAFT. Promoting it to CURRENT is a separate,
deliberate act (`--promote`), because CURRENT is what every new tenant gets.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from collections.abc import Iterator
from typing import cast

import psycopg

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import get_settings  # noqa: E402
from src.provisioning.objectstore import (  # noqa: E402
    PublishedObject,
    S3ObjectStore,
)

A_DOCUMENTS = ROOT / "reference" / "A1-A7Documents"
B_DOCUMENTS = ROOT / "reference" / "B-Documents" / "inafin_mock_categoryB"

#: Never published. See this module's docstring — the first entry is the one
#: that matters, and it is a correctness rule, not tidiness.
_EXCLUDED_FILENAMES = frozenset({
    "ANOMALY_KEY.json",
    "MANIFEST.json",
    "README.md",
    ".DS_Store",
})

#: Sample-set scaffolding, not documents.
_EXCLUDED_DIRS = frozenset({"_generator"})

_REF_RE = re.compile(r"^([A-D][0-9]\.[0-9]{2})")


def _ref_of(name: str) -> str | None:
    """`A2.01_Certificate...pdf` -> `A2.01`; `B1.01_GSTR-1` -> `B1.01`."""
    m = _REF_RE.match(name)
    return m.group(1) if m else None


def _sample_files() -> Iterator[tuple[str, pathlib.Path]]:
    """Yield `(register_ref, path)` for every publishable sample.

    Two layouts, because the two sample sets arrived differently and neither
    is reorganised here: A-documents are flat files named by ref; B-documents
    are `<group>/<ref>_<desc>/<files>`. `reference/` stays the as-received
    copy — the same rule `stage_bronze_fixtures.py` follows.
    """
    if A_DOCUMENTS.is_dir():
        for path in sorted(A_DOCUMENTS.iterdir()):
            if not path.is_file() or path.name in _EXCLUDED_FILENAMES:
                continue
            ref = _ref_of(path.name)
            if ref:
                yield ref, path

    if B_DOCUMENTS.is_dir():
        for group in sorted(p for p in B_DOCUMENTS.iterdir() if p.is_dir()):
            if group.name in _EXCLUDED_DIRS:
                continue
            for ref_dir in sorted(p for p in group.iterdir() if p.is_dir()):
                ref = _ref_of(ref_dir.name)
                if not ref:
                    continue
                for path in sorted(ref_dir.iterdir()):
                    if path.is_file() and path.name not in _EXCLUDED_FILENAMES:
                        yield ref, path


def _constraint_block(
    sql_domain: object, pattern: object, allowed_values: object,
    min_value: object, max_value: object, max_length: object,
    numeric_precision: object, numeric_scale: object,
) -> dict[str, object] | None:
    """A field's `constraints` object, or None if it carries none.

    None rather than an all-null object: a client checking "does this field
    have a constraint block at all" gets a clean absence instead of having to
    inspect every key. Migration 041's columns are the source — nothing here
    is computed, only reshaped for JSON (the array and the two bounds).
    """
    if all(
        v is None
        for v in (sql_domain, pattern, allowed_values, min_value, max_value,
                   max_length, numeric_precision)
    ):
        return None
    block: dict[str, object] = {}
    if sql_domain is not None:
        block["sql_domain"] = str(sql_domain)
    if pattern is not None:
        block["pattern"] = str(pattern)
    if allowed_values is not None:
        block["allowed_values"] = list(cast("list[str]", allowed_values))
    if min_value is not None:
        block["min_value"] = str(min_value)
    if max_value is not None:
        block["max_value"] = str(max_value)
    if max_length is not None:
        block["max_length"] = int(str(max_length))
    if numeric_precision is not None:
        block["numeric_precision"] = int(str(numeric_precision))
    if numeric_scale is not None:
        block["numeric_scale"] = int(str(numeric_scale))
    return block


def _render_schema(
    conn: psycopg.Connection[tuple[object, ...]], release: str
) -> dict[str, bytes]:
    """One JSON document per in-scope type, rendered from the catalogue.

    Stable key order and a trailing newline so that re-publishing an unchanged
    release produces byte-identical objects — which is what makes the sha256 in
    `schema_artifact` a meaningful comparison rather than noise.

    `constraints` (migration 041) is emitted ONLY for fields that carry one,
    and — the load-bearing part — only DERIVED types have any to carry, because
    a DECLARED type's field list comes from a registry grammar cell with no
    table behind it. Omitting the key for those types is the honest answer;
    emitting `"constraints": null` would read as "the database accepts
    anything", when the truth is "we do not know". `provenance` already tells
    a client which case they are in.
    """
    fields: dict[str, list[dict[str, object]]] = {}
    for (
        code, ordinal, name, scope, data_type, required, label,
        sql_domain, pattern, allowed_values, min_value, max_value,
        max_length, numeric_precision, numeric_scale,
    ) in conn.execute(
        "SELECT doc_type_code, ordinal, field_name, scope, data_type, required,"
        "       source_label, sql_domain, pattern, allowed_values,"
        "       min_value, max_value, max_length, numeric_precision, numeric_scale"
        "  FROM platform_ref.document_type_field"
        " ORDER BY doc_type_code, ordinal"
    ).fetchall():
        entry: dict[str, object] = {
            "ordinal": int(str(ordinal)),
            "name": str(name),
            "scope": str(scope),
            "type": str(data_type),
            "required": bool(required),
        }
        if label is not None:
            entry["source_label"] = str(label)
        constraints = _constraint_block(
            sql_domain, pattern, allowed_values, min_value, max_value,
            max_length, numeric_precision, numeric_scale,
        )
        if constraints is not None:
            entry["constraints"] = constraints
        fields.setdefault(str(code), []).append(entry)

    rules: dict[str, list[dict[str, object]]] = {}
    for code, constraint_name, expression, columns in conn.execute(
        "SELECT doc_type_code, constraint_name, expression, columns"
        "  FROM platform_ref.document_type_rule"
        " ORDER BY doc_type_code, constraint_name"
    ).fetchall():
        rules.setdefault(str(code), []).append({
            "constraint_name": str(constraint_name),
            "expression": str(expression),
            "columns": list(cast("list[str]", columns)),
        })

    out: dict[str, bytes] = {}
    for code, name, kind, provenance in conn.execute(
        "SELECT s.doc_type_code, d.name, s.schema_kind, s.provenance"
        " FROM platform_ref.document_type_schema s"
        " JOIN platform_ref.document_type d ON d.doc_type_code = s.doc_type_code"
        " ORDER BY s.doc_type_code"
    ).fetchall():
        body: dict[str, object] = {
            "release": release,
            "doc_type_code": str(code),
            "name": str(name),
            "schema_kind": str(kind),
            "provenance": str(provenance),
            "fields": fields.get(str(code), []),
        }
        type_rules = rules.get(str(code))
        if type_rules:
            body["rules"] = type_rules
        out[str(code)] = (
            json.dumps(body, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
        ).encode()
    return out


def publish(release: str, *, promote: bool, dry_run: bool) -> int:
    settings = get_settings()
    store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket_prefix=settings.s3_bucket_prefix,
        retention_days=settings.bronze_retention_days,
    )
    super_dsn = settings.pg_super_dsn.rsplit("/", 1)[0] + "/tenant_db"

    with psycopg.connect(super_dsn, autocommit=False) as conn:
        schemas = _render_schema(conn, release)

        code_by_ref = {
            str(ref): str(code)
            for ref, code in conn.execute(
                "SELECT register_ref, doc_type_code FROM platform_ref.document_type_ref"
            ).fetchall()
        }
        in_scope = {
            str(c) for (c,) in conn.execute(
                "SELECT doc_type_code FROM platform_ref.document_type WHERE in_scope"
            ).fetchall()
        }

        samples: list[tuple[str, pathlib.Path]] = []
        unmapped: list[str] = []
        for ref, path in _sample_files():
            code = code_by_ref.get(ref)
            if code is None or code not in in_scope:
                unmapped.append(f"{ref}/{path.name}")
                continue
            samples.append((code, path))

        print(f"release {release}: {len(schemas)} schema(s), {len(samples)} sample(s)")
        if unmapped:
            # Reported, never silently dropped: an unmapped ref means either a
            # renamed sample folder or a registry gap, and both are worth
            # knowing about.
            print(f"  {len(unmapped)} sample(s) skipped, ref not in registry:")
            for u in unmapped[:10]:
                print(f"    {u}")
        if dry_run:
            print("  dry run — nothing uploaded, nothing written")
            return 0

        store.ensure_platform_bucket()
        conn.execute(
            "INSERT INTO platform_ref.schema_release (version, status, notes)"
            " VALUES (%s, 'DRAFT', %s)"
            " ON CONFLICT (version) DO NOTHING",
            (release, f"published {dt.date.today().isoformat()}"),
        )

        for code, body in schemas.items():
            obj = store.put_platform_artifact(
                release=release, kind="SCHEMA", doc_type_code=code,
                filename=f"{code}.json", data=body,
            )
            _record(conn, release, code, "SCHEMA", f"{code}.json", "json", obj)

        for code, path in samples:
            data = path.read_bytes()
            obj = store.put_platform_artifact(
                release=release, kind="SAMPLE", doc_type_code=code,
                filename=path.name, data=data,
            )
            _record(
                conn, release, code, "SAMPLE", path.name,
                path.suffix.lstrip(".").lower() or "bin", obj,
            )

        if promote:
            # SUPERSEDED, never deleted: tenants pinned to the old release keep
            # being served it. Migration 039's header explains why.
            conn.execute(
                "UPDATE platform_ref.schema_release SET status = 'SUPERSEDED'"
                " WHERE status = 'CURRENT' AND version <> %s",
                (release,),
            )
            conn.execute(
                "UPDATE platform_ref.schema_release SET status = 'CURRENT'"
                " WHERE version = %s",
                (release,),
            )
            print(f"  promoted {release} to CURRENT")

        conn.commit()
    print("  done")
    return 0


def _record(
    conn: psycopg.Connection[tuple[object, ...]],
    release: str,
    code: str,
    kind: str,
    filename: str,
    content_format: str,
    obj: PublishedObject,
) -> None:
    conn.execute(
        "INSERT INTO platform_ref.schema_artifact"
        " (release_version, doc_type_code, kind, filename, content_format,"
        "  object_bucket, object_key, object_version_id, sha256, size_bytes)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (release_version, object_key) DO UPDATE SET"
        "   object_version_id = EXCLUDED.object_version_id,"
        "   sha256 = EXCLUDED.sha256, size_bytes = EXCLUDED.size_bytes",
        (
            release, code, kind, filename, content_format,
            obj.bucket, obj.key, obj.version_id, obj.content_hash,
            obj.size_bytes,
        ),
    )


def main() -> int:
    p = argparse.ArgumentParser(prog="publish_schema_release")
    p.add_argument("release", help="release label, e.g. v1")
    p.add_argument(
        "--promote", action="store_true",
        help="make this the CURRENT release every new tenant gets",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report what would be published, upload nothing",
    )
    args = p.parse_args()
    if not re.fullmatch(r"v[0-9]+", args.release):
        print("ERROR: release must look like v1, v2, ...", file=sys.stderr)
        return 1
    return publish(args.release, promote=args.promote, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
