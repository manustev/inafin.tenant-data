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

import pathlib
import sys

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
