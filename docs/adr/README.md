# Architecture Decision Records

This folder records design decisions for `inafin-tenant-data` that are not
already captured in `TYPED-TABLES-PLAN.md`, `ARCHITECTURE.md`, or `TODO.md` —
in particular, decisions that arise from a request or requirements handoff
from an external consumer (`inafin-reconciliation-engine`, `inafin-api`,
`inafin-portal`, `inafinplatform/v2`, ...) rather than from this repo's own
backlog.

## When to write one

Write an ADR when a decision changes something structural: a new role, a new
schema, a new cross-repo contract (a `v1_` view, a grant boundary), or a
reversal of a prior decision. Do not write one for routine additive work
that already fits an existing pattern (e.g. "add doc type X under existing
mechanism Y" — that is a CSV row, not a decision).

## Format

Copy `0000-template.md`. Number sequentially, zero-padded to 4 digits
(`0001-...`, `0002-...`). File name is `NNNN-short-kebab-title.md`.

Each ADR has:

- **Status** — `Proposed`, `Accepted`, `Superseded by NNNN`, or `Rejected`.
- **Context** — what prompted this, who asked, what constraint or invariant
  is in tension.
- **Decision** — what was decided, stated as a rule someone can follow.
- **Consequences** — what this enables, what it costs, what it explicitly
  defers or rules out.

An ADR is never edited to reverse a decision — write a new one and mark the
old one `Superseded by NNNN`, the same discipline this repo already applies
to migrations.
