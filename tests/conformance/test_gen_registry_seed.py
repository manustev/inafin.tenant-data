"""Generation-time validation for `scripts/gen_registry_seed.py`.

`_check_extraction_specs` gives `extraction_spec` the exact guarantee
`_check_contracts` already gives `field_contract` (`src/silver/contract.py`):
a cell that would be rejected at runtime cannot be committed to
`registry/document_types.csv` in the first place, because the generator
imports the same parser (`src/extraction/spec.py:parse_extraction_spec`) the
runtime uses (`src/extraction/registry.py`'s `build_extractor_registry`).

This is the mutation check `streamed-marinating-gray.md`'s verification
section asks for, run automatically rather than by hand: corrupt one real
`extraction_spec` cell, confirm generation refuses (and writes nothing), then
prove the untouched real CSV still generates cleanly — restoring is implicit
because the test never touches the real CSV file on disk, only an in-memory
copy written to a temp path.
"""

from __future__ import annotations

import csv
import pathlib
import sys

import pytest

pytestmark = pytest.mark.conformance

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import gen_registry_seed as gen  # noqa: E402


def _load_rows() -> tuple[list[str], list[dict[str, str]]]:
    with gen.CSV_PATH.open(newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _write_rows(path: pathlib.Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _patch_output_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    # main() prints each path relative to ROOT purely for its own log line —
    # ROOT is patched alongside so that formatting does not blow up on a path
    # outside the real repo tree.
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.setattr(gen, "OUT_PATH", tmp_path / "004_out.sql")
    monkeypatch.setattr(gen, "CADENCE_PATH", tmp_path / "005_out.sql")
    monkeypatch.setattr(gen, "CONTRACT_PATH", tmp_path / "009_out.sql")
    monkeypatch.setattr(gen, "EXTRACTION_SPEC_PATH", tmp_path / "016_out.sql")
    monkeypatch.setattr(gen, "DISPATCH_MECHANISM_PATH", tmp_path / "017_out.sql")
    monkeypatch.setattr(gen, "SCHEMA_CATALOGUE_PATH", tmp_path / "036_out.sql")
    # KNOWN_SEALED is keyed by the REAL path objects, so once the paths above
    # are redirected nothing matches it and every writer takes the ordinary
    # write branch — which is what these tests want to exercise. Cleared
    # anyway, so that a future entry added there cannot silently turn one of
    # these assertions into a no-op.
    monkeypatch.setattr(gen, "KNOWN_SEALED", {})


def test_a_malformed_extraction_spec_cell_is_rejected_at_generation_time(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    fieldnames, rows = _load_rows()
    lut = next(r for r in rows if r["doc_type_code"] == "LUT")
    assert lut["extraction_spec"], "fixture assumption: LUT has a real extraction_spec to corrupt"
    lut["extraction_spec"] = "authority=GSTN;fields=not-a-valid-field-token"

    corrupt_csv = tmp_path / "document_types.csv"
    _write_rows(corrupt_csv, fieldnames, rows)

    monkeypatch.setattr(gen, "CSV_PATH", corrupt_csv)
    _patch_output_paths(monkeypatch, tmp_path)

    rc = gen.main()
    err = capsys.readouterr().err

    assert rc == 1
    assert "LUT" in err
    assert not gen.EXTRACTION_SPEC_PATH.exists(), (
        "a rejected spec must not reach the generated migration"
    )


def test_an_out_of_scope_row_carrying_an_extraction_spec_is_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The scope guard, not just the grammar: an extraction_spec on a
    CORPUS-stream (out-of-scope) row is unreachable code in table form —
    same reasoning `_check_contracts` already applies to `field_contract`."""
    fieldnames, rows = _load_rows()
    corpus_row = next(r for r in rows if r["stream"] == "CORPUS")
    corpus_row["extraction_spec"] = "fields="

    corrupt_csv = tmp_path / "document_types.csv"
    _write_rows(corrupt_csv, fieldnames, rows)

    monkeypatch.setattr(gen, "CSV_PATH", corrupt_csv)
    _patch_output_paths(monkeypatch, tmp_path)

    rc = gen.main()
    err = capsys.readouterr().err

    assert rc == 1
    assert "out-of-scope" in err


def test_a_dispatch_mechanism_naming_the_wrong_route_is_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """`dispatch_mechanism=PDF_EXTRACTION` is only correct when
    `build_extractor_registry` would actually serve an extractor for that
    code — same "spec is a projection of the code, not free invention" gate
    `_check_extraction_specs`/`_check_table_names` already give the other
    registry columns."""
    fieldnames, rows = _load_rows()
    row = next(r for r in rows if r["doc_type_code"] == "GSTR_1")
    assert not row["extraction_spec"], (
        "fixture assumption: GSTR_1 has no extraction_spec and is not a "
        "PDF-shaped register type, so PDF_EXTRACTION is wrong for it"
    )
    row["dispatch_mechanism"] = "PDF_EXTRACTION"

    corrupt_csv = tmp_path / "document_types.csv"
    _write_rows(corrupt_csv, fieldnames, rows)

    monkeypatch.setattr(gen, "CSV_PATH", corrupt_csv)
    _patch_output_paths(monkeypatch, tmp_path)

    rc = gen.main()
    err = capsys.readouterr().err

    assert rc == 1
    assert "GSTR_1" in err
    assert not gen.DISPATCH_MECHANISM_PATH.exists(), (
        "a rejected dispatch_mechanism must not reach the generated migration"
    )


def test_an_out_of_scope_row_carrying_a_dispatch_mechanism_is_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    fieldnames, rows = _load_rows()
    corpus_row = next(r for r in rows if r["stream"] == "CORPUS")
    corpus_row["dispatch_mechanism"] = "REGISTER_LOADER"

    corrupt_csv = tmp_path / "document_types.csv"
    _write_rows(corrupt_csv, fieldnames, rows)

    monkeypatch.setattr(gen, "CSV_PATH", corrupt_csv)
    _patch_output_paths(monkeypatch, tmp_path)

    rc = gen.main()
    err = capsys.readouterr().err

    assert rc == 1
    assert "out-of-scope" in err


def test_the_real_csv_still_generates_cleanly(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restore half of the mutation check: the actual, uncorrupted
    `registry/document_types.csv` must generate without error — proving the
    prior tests' rejection is about the corruption, not a broken generator."""
    _patch_output_paths(monkeypatch, tmp_path)
    rc = gen.main()
    assert rc == 0
    assert gen.EXTRACTION_SPEC_PATH.exists()
    assert gen.DISPATCH_MECHANISM_PATH.exists()
