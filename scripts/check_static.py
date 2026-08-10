#!/usr/bin/env python
"""Static gates — ARCHITECTURE.md 10, "Static gates".

Source-level rules that no runtime test can catch, because the code path they
forbid may simply never execute in CI. Each one corresponds to a way the
isolation boundary has been broken in real systems.

    python scripts/check_static.py     # exits 1 on any violation
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    why: str
    include: tuple[str, ...] = ("src",)
    exempt: tuple[str, ...] = ()


RULES: tuple[Rule, ...] = (
    Rule(
        name="no-session-scoped-set-role",
        pattern=re.compile(r"""SET\s+ROLE""", re.IGNORECASE),
        why=(
            "Session-scoped SET ROLE survives COMMIT and leaks across tenants on a "
            "pooled backend. Use SET LOCAL ROLE, which dies with the transaction."
        ),
        exempt=("core/pool.py",),  # uses SET LOCAL ROLE; checked by the next rule
    ),
    Rule(
        name="no-session-scoped-set-config",
        pattern=re.compile(r"""set_config\([^)]*,\s*false\s*\)""", re.IGNORECASE),
        why=(
            "set_config(..., false) is session-scoped and outlives the transaction "
            "on a pooled connection. Pass true for transaction-local."
        ),
    ),
    Rule(
        name="pool-not-constructed-outside-core",
        pattern=re.compile(r"\bAsyncConnectionPool\s*\("),
        why=(
            "Only TenantScopedPool may construct a pool. A pool built elsewhere "
            "would hand out connections with no role assumption and no guard."
        ),
        exempt=("core/pool.py",),
    ),
    Rule(
        name="no-schema-name-string-building",
        pattern=re.compile(r"""["']t_["']\s*\+|f["']t_\{"""),
        why=(
            "Schema names are derived only in src/core/identifiers.py, which "
            "validates the slug first. Building one by concatenation skips that gate."
        ),
        exempt=("core/identifiers.py",),
    ),
    Rule(
        name="no-bypassrls-in-source",
        pattern=re.compile(r"\bBYPASSRLS\b"),
        why=(
            "No runtime role may bypass row security. NOBYPASSRLS declarations "
            "belong in bootstrap/ and migrations/, not in application code."
        ),
    ),
    Rule(
        name="no-superuser-dsn-in-source",
        pattern=re.compile(r"pg_super_dsn", re.IGNORECASE),
        why=(
            "The superuser DSN is for bootstrap and the test control connection "
            "only. A deployed code path must never reference it."
        ),
        exempt=("core/config.py",),
    ),
)


def _iter_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def main() -> int:
    violations: list[str] = []

    for path in _iter_files():
        rel = path.relative_to(SRC).as_posix()
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        for rule in RULES:
            if any(rel.endswith(x) for x in rule.exempt):
                continue
            for lineno, line in enumerate(lines, start=1):
                stripped = line.strip()
                # Comments and docstring prose describe these rules constantly;
                # only executable lines are in scope.
                if stripped.startswith("#") or stripped.startswith(('"', "'")):
                    continue
                if rule.pattern.search(line):
                    violations.append(
                        f"[{rule.name}] src/{rel}:{lineno}\n"
                        f"    {stripped}\n"
                        f"    -> {rule.why}"
                    )

    # SET LOCAL ROLE must be exactly that, in the one file allowed to say it.
    pool = (SRC / "core" / "pool.py").read_text(encoding="utf-8")
    if "SET LOCAL ROLE" not in pool:
        violations.append(
            "[pool-uses-set-local-role] src/core/pool.py\n"
            "    -> TenantScopedPool must assume the tenant role with SET LOCAL ROLE."
        )
    if "assert_tenant_context" not in pool:
        violations.append(
            "[pool-calls-guard] src/core/pool.py\n"
            "    -> TenantScopedPool must call app.assert_tenant_context in the preamble."
        )
    if "prepare_threshold" not in pool:
        violations.append(
            "[pool-disables-prepared-statements] src/core/pool.py\n"
            "    -> prepare_threshold must be None under PgBouncer transaction pooling; "
            "prepared statements do not survive a backend switch."
        )

    if violations:
        for v in violations:
            print(v)
        print(f"\n{len(violations)} static gate violation(s)")
        return 1
    print(f"static gates OK — {len(RULES)} rules over {len(_iter_files())} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
