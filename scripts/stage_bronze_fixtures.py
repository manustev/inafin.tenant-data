#!/usr/bin/env python3
"""Stage the Category B sample data into `LocalFixtureConnector`'s expected
tree: `<fixture_root>/<tenant_slug>/<ref>/<filename>`.

WHY THIS EXISTS. `reference/B-Documents/inafin_mock_categoryB/` is organised
by category/group (`B1_GSTN_Filed_Returns/B1.01_GSTR-1/*.json`) — a sensible
layout for a human reading the sample set, but not the layout
`src/connectors/local_fixture.py` resolves against, which is keyed by
TENANT first (a real deployment holds fixture/live data for many tenants,
never one). This script is the one place that reshapes received sample data
into the connector's actual folder contract — `reference/B-Documents/` itself
is left untouched, so it stays the faithful, as-received copy of what Steve
supplied.

USAGE.

    python3 scripts/stage_bronze_fixtures.py [--tenant-slug SLUG]

Idempotent and safe to re-run: every file is copied fresh and overwrites
whatever was already at the destination (mtimes aren't compared), so
re-running after `reference/B-Documents/` is regenerated
(`_generator/run_all.py`) picks up the change without manual cleanup, and the
destination should never be hand-edited independently of this script.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "reference" / "B-Documents" / "inafin_mock_categoryB"
DEFAULT_DEST_ROOT = REPO_ROOT / "fixtures" / "bronze_source"
DEFAULT_TENANT_SLUG = "vardhman"

#: Only files with these extensions are staged — matches
#: `src/bronze/filecheck.py:ALLOWED_EXTENSIONS` minus "ndjson" (none of the
#: sample data ships as ndjson) so nothing lands in the fixture tree that
#: intake would refuse anyway.
_STAGED_EXTENSIONS = {".json", ".csv", ".pdf"}


def stage(source_root: Path, dest_root: Path, tenant_slug: str) -> int:
    """Copies every ref-named subfolder's files under `source_root`'s group
    folders into `dest_root/tenant_slug/<ref>/`. Returns the file count
    staged, for the CLI's summary line.

    A "ref-named subfolder" is any directory whose name starts with
    `B<digit>.<digit digit>_` (`B1.01_GSTR-1`, `B4.03_EWayBill_Threshold_
    History`, ...) — the convention every folder in the sample set already
    follows, confirmed by inspection before writing this script, not
    assumed.
    """
    staged = 0
    group_dirs = sorted(p for p in source_root.iterdir() if p.is_dir())
    for group_dir in group_dirs:
        ref_dirs = sorted(p for p in group_dir.iterdir() if p.is_dir())
        for ref_dir in ref_dirs:
            ref = _ref_from_folder_name(ref_dir.name)
            if ref is None:
                continue
            dest_dir = dest_root / tenant_slug / ref
            dest_dir.mkdir(parents=True, exist_ok=True)
            for src_file in sorted(ref_dir.iterdir()):
                if not src_file.is_file() or src_file.suffix.lower() not in _STAGED_EXTENSIONS:
                    continue
                shutil.copy2(src_file, dest_dir / src_file.name)
                staged += 1
    return staged


def _ref_from_folder_name(name: str) -> str | None:
    """`"B1.01_GSTR-1"` -> `"B1.01"`. `None` for a folder that doesn't
    follow the `<ref>_<description>` convention (defensive — every folder in
    the sample set does, but a script that silently mis-stages a renamed
    folder as ref `""` is worse than one that skips it and lets the file
    count catch the miss)."""
    prefix, sep, _ = name.partition("_")
    if not sep or prefix[:1] != "B" or "." not in prefix:
        return None
    return prefix


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant-slug", default=DEFAULT_TENANT_SLUG,
        help=f"Fixture-tree tenant folder to stage under (default: {DEFAULT_TENANT_SLUG!r} — "
             f"the mock taxpayer's data is not tied to any real tenant, so any slug works).",
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE,
        help="Category B sample-data root (default: reference/B-Documents/inafin_mock_categoryB).",
    )
    parser.add_argument(
        "--dest-root", type=Path, default=DEFAULT_DEST_ROOT,
        help="Fixture tree root — must match Settings.source_fixture_root "
             "(default: fixtures/bronze_source).",
    )
    args = parser.parse_args()

    if not args.source.is_dir():
        print(f"error: source root not found: {args.source}", file=sys.stderr)
        return 1

    count = stage(args.source, args.dest_root, args.tenant_slug)
    print(
        f"staged {count} files -> {args.dest_root}/{args.tenant_slug}/<ref>/"
        f" (source: {args.source})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
