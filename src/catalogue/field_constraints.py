"""Derive the DATABASE's own constraints for every published schema field.

WHAT GAP THIS CLOSES. `document_schema.py` publishes a field's coarse `Kind`
— text, date, decimal — because that is the parse rule a CSV cell goes
through. It says nothing about what the COLUMN will accept, and the two are
not the same question: `supplier_gstin` is published as `text` and is
`platform_ref.gstin`, a domain whose CHECK enforces the embedded PAN;
`gst_rate` is published as `decimal` and is `numeric(6,3)` bounded 0-100;
`itc_eligibility` is published as `text` and accepts four values. A client
generating an export that satisfies the published schema could therefore be
refused by Postgres at ingestion — found by the ERP upload E2E suite
(2026-09-01) across 70 document types, and reaching the caller as an opaque
500 until `src/dispatch/trigger.py` learned to classify integrity errors.

**THE CONSTRAINT IS READ FROM THE DATABASE, NEVER RESTATED HERE.** That is
the whole design. `src/silver/registers/spec.py` deliberately does NOT carry
CHECK vocabularies or the GSTIN shape, and its docstring gives the reason:
re-declaring them would be a second copy of a rule the database already
enforces, free to disagree with it. Publishing a hand-maintained list here
would make exactly that mistake one layer further out, where nothing would
catch the drift. So this module reads `pg_constraint` and `pg_type` and
projects what it finds. A migration that alters a column changes the
published contract on the next generation, and
`tests/conformance/test_field_constraints.py` re-runs this derivation against
live DDL and fails if the stored rows have fallen behind — the same gate
shape `test_register_specs.py::test_spec_matches_the_table` uses.

WHAT IS DELIBERATELY NOT PARSED. A check this module cannot reduce to a
field-level shape is NOT dropped and NOT guessed at — it is published whole,
as a `SchemaRule`, with the columns it constrains. Two kinds land there:

  * genuinely multi-column business rules. `sales_register_line`'s
    intra-versus-inter-state rule (IGST positive implies CGST and SGST zero)
    is real, is a rule a client trips over, and belongs to no single field.
  * anything whose expression shape is not in `_parse_check`. Publishing the
    expression verbatim is honest — a client can read it — where inventing a
    field-level approximation of it would not be.

ENVELOPE COLUMNS ARE EXCLUDED, at both grains. `doc_type_code = 'X'`,
`valid_to > valid_from` and the `superseded_at`/`modified_at` pairing are all
real constraints that a tenant can neither satisfy nor violate: those columns
are written by the loader, not supplied in the export. `document_type_field`
already subtracts them from the published field list (see
`src/silver/registers/spec.py`'s `ENVELOPE_COLUMNS`), and a constraint is
published here only if every column it touches survived that subtraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

import psycopg

#: Where a published field's `scope` says its column lives. A type with a
#: header/line split stores LINE fields in `<table_name>_line` — the convention
#: shared migration 010's header fixes, rather than a second registry column
#: that could disagree with it. SALES_REGISTER is the only DERIVED type with
#: a split today; the mapping is written generally because the convention is.
_LINE_SCOPE = "LINE"


@dataclass(frozen=True, slots=True)
class FieldConstraint:
    """What the database will accept in one published field's column.

    Every attribute is optional because Postgres constrains different columns
    in different ways and most columns carry none of these. `None` means "no
    such constraint on this column", never "not looked at" — the derivation
    visits every published field of every DERIVED type.
    """

    doc_type_code: str
    scope: str
    field_name: str

    sql_domain: str | None = None
    """The schema-qualified domain, e.g. `platform_ref.gstin`. Published as a
    NAME as well as its parsed shape below, because the name is stable and
    self-describing where a regex is neither."""

    pattern: str | None = None
    """A POSIX regex the value must match, from a `~` check on the column or
    on its domain. Carried verbatim as Postgres reports it."""

    allowed_values: tuple[str, ...] | None = None
    """The complete accepted vocabulary, from `= ANY (ARRAY[...])` or a
    single `= 'X'`. In the order the constraint declares."""

    min_value: Decimal | None = None
    max_value: Decimal | None = None
    """INCLUSIVE bounds. A strict `> N` on an integer column is published as
    `min_value = N + 1`, which is exactly equivalent rather than an
    approximation; a strict bound on a non-integer column has no exact
    inclusive form and is published as a `SchemaRule` instead of being
    rounded into one."""

    max_length: int | None = None
    """`character_maximum_length` — the fixed-width codes (`character(2)` for
    a state code, `character(3)` for a currency) the E2E finding names."""

    numeric_precision: int | None = None
    numeric_scale: int | None = None
    """Total digits and decimal places. `numeric(18,2)` silently rounds a
    third decimal place but REJECTS an 19-digit value, so precision is the
    half a client must know about."""


@dataclass(frozen=True, slots=True)
class SchemaRule:
    """A constraint that is not reducible to one field's shape.

    See the module docstring. `expression` is `pg_get_constraintdef`'s text
    with its casts stripped for legibility and nothing else changed — it is
    evidence, not prose, and a client reading it sees exactly what the
    database will apply.
    """

    doc_type_code: str
    constraint_name: str
    expression: str
    columns: tuple[str, ...]


_CAST = re.compile(r"::[a-zA-Z_][a-zA-Z0-9_ ]*(\[\])?")
_WS = re.compile(r"\s+")


def _normalise(expr: str) -> str:
    """`pg_get_constraintdef` text with casts removed and spacing collapsed.

    Postgres re-renders every check from its parse tree, so the text is already
    canonical — `'CN'::text`, `(0)::numeric`, `ARRAY[...]::text[]`. Stripping
    the casts is what makes one regex per SHAPE sufficient instead of one per
    shape-and-column-type, and it loses nothing: the column's type is read
    separately, from `information_schema`.
    """
    inner = expr.strip()
    if inner.upper().startswith("CHECK "):
        inner = inner[len("CHECK "):].strip()
    inner = _CAST.sub("", inner)
    return _WS.sub(" ", inner).strip()


def _strip_parens(s: str) -> str:
    """Drop balanced outer parentheses, which Postgres nests generously."""
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        for i, ch in enumerate(s):
            depth += (ch == "(") - (ch == ")")
            if depth == 0 and i < len(s) - 1:
                return s  # the outer parens are not a matched pair
        s = s[1:-1].strip()
    return s


_RE_PATTERN = re.compile(r"^(?P<col>\w+) ~ '(?P<pat>.*)'$")
_RE_NULL_OR = re.compile(r"^\((?P<col>\w+) IS NULL\) OR \((?P<rest>.*)\)$")
_RE_ANY = re.compile(r"^(?P<col>\w+) = ANY \(ARRAY\[(?P<vals>.*)\]\)$")
_RE_EQ = re.compile(r"^(?P<col>\w+) = '(?P<val>.*)'$")
#: A numeric literal, with the parentheses Postgres leaves behind when a cast
#: is stripped: `(0)::numeric` normalises to `(0)`, not to `0`. Missing this
#: is why the first cut of this parser silently failed to read `tax_rate`'s
#: 0-100 bounds — the single most useful constraint in the whole derivation.
_NUM = r"\(?(-?[\d.]+)\)?"

_RE_RANGE = re.compile(
    rf"^\((?P<c1>\w+) >= {_NUM}\) AND \((?P<c2>\w+) <= {_NUM}\)$"
)
_RE_GTE = re.compile(rf"^(?P<col>\w+) >= {_NUM}$")
_RE_LTE = re.compile(rf"^(?P<col>\w+) <= {_NUM}$")
_RE_GT = re.compile(r"^(?P<col>\w+) > \(?(-?\d+)\)?$")
_RE_VALUES = re.compile(r"'((?:[^']|'')*)'")


@dataclass(frozen=True, slots=True)
class _Shape:
    """What one parseable check contributes to a `FieldConstraint`."""

    pattern: str | None = None
    allowed_values: tuple[str, ...] | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None


def parse_check(expression: str, *, subject: str, is_integer: bool = False) -> _Shape | None:
    """One check's field-level shape, or None if it has none.

    `subject` is the column name, or the literal `VALUE` when parsing a
    DOMAIN's constraint — the two are the same grammar with a different noun,
    which is why the GSTIN domain's regex and a table's `fy ~ '...'` check go
    through one function.

    Returning None is a real answer and the caller publishes the check as a
    `SchemaRule` rather than discarding it. That is the load-bearing half:
    a shape this function does not recognise must never become silence.
    """
    body = _strip_parens(_normalise(expression))

    # `(col IS NULL) OR (<check>)` — a nullable column's constraint. The
    # nullability is already published as `required`, so only the inner
    # check carries new information.
    if (m := _RE_NULL_OR.match(body)) and m.group("col") == subject:
        body = _strip_parens(m.group("rest"))

    if (m := _RE_PATTERN.match(body)) and m.group("col") == subject:
        return _Shape(pattern=m.group("pat"))

    if (m := _RE_ANY.match(body)) and m.group("col") == subject:
        values = tuple(v.replace("''", "'") for v in _RE_VALUES.findall(m.group("vals")))
        return _Shape(allowed_values=values) if values else None

    if (m := _RE_EQ.match(body)) and m.group("col") == subject:
        return _Shape(allowed_values=(m.group("val").replace("''", "'"),))

    if (m := _RE_RANGE.match(body)) and m.group("c1") == subject == m.group("c2"):
        return _Shape(min_value=Decimal(m.group(2)), max_value=Decimal(m.group(4)))

    if (m := _RE_GTE.match(body)) and m.group("col") == subject:
        return _Shape(min_value=Decimal(m.group(2)))

    if (m := _RE_LTE.match(body)) and m.group("col") == subject:
        return _Shape(max_value=Decimal(m.group(2)))

    # `col > N` is exactly `col >= N + 1` on an integer column and only
    # there. On a numeric column there is no least allowed value to publish,
    # so this falls through to None and the check is carried as a rule.
    if is_integer and (m := _RE_GT.match(body)) and m.group("col") == subject:
        return _Shape(min_value=Decimal(m.group(2)) + 1)

    return None


#: Published fields, and the table each type's columns live in. DERIVED only:
#: a DECLARED type's field list comes from a registry grammar cell and owns no
#: table, so there is no constraint to read and publishing an empty one would
#: imply the database accepts anything. `document_schema.py`'s `provenance`
#: already says which is which, so this filter reads it rather than restating
#: the rule.
_PUBLISHED_FIELDS = """
    SELECT f.doc_type_code, f.scope, f.field_name, d.table_name
      FROM platform_ref.document_type_field f
      JOIN platform_ref.document_type_schema s USING (doc_type_code)
      JOIN platform_ref.document_type d USING (doc_type_code)
     WHERE s.provenance = 'DERIVED' AND d.table_name IS NOT NULL
     ORDER BY f.doc_type_code, f.ordinal
"""

#: Per-column type facts. The domain is resolved by name so the published
#: value is `platform_ref.gstin` rather than its underlying `text`, which is
#: the whole point — the underlying type is what `data_type` already says.
_COLUMN_FACTS = """
    SELECT c.relname,
           a.attname,
           CASE WHEN t.typtype = 'd'
                THEN tn.nspname || '.' || t.typname END,
           information_schema._pg_char_max_length(
               information_schema._pg_truetypid(a.*, t.*),
               information_schema._pg_truetypmod(a.*, t.*)),
           information_schema._pg_numeric_precision(
               information_schema._pg_truetypid(a.*, t.*),
               information_schema._pg_truetypmod(a.*, t.*)),
           information_schema._pg_numeric_scale(
               information_schema._pg_truetypid(a.*, t.*),
               information_schema._pg_truetypmod(a.*, t.*)),
           bt.typcategory = 'N' AND bt.typname IN ('int2', 'int4', 'int8')
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_type t ON t.oid = a.atttypid
      JOIN pg_namespace tn ON tn.oid = t.typnamespace
      LEFT JOIN pg_type bt ON bt.oid = COALESCE(NULLIF(t.typbasetype, 0), t.oid)
     WHERE n.nspname = %s AND a.attnum > 0 AND NOT a.attisdropped
       AND c.relname = ANY(%s)
"""

#: Every CHECK on those tables, with the columns it touches. `conkey` is the
#: attribution, NOT the expression text: Postgres records exactly which
#: attributes a constraint references, so a rule is assigned to its columns
#: without parsing anything. Only the SHAPE is parsed, and only after the
#: column is known.
_TABLE_CHECKS = """
    SELECT c.relname,
           con.conname,
           pg_get_constraintdef(con.oid),
           (SELECT array_agg(a.attname ORDER BY k.ord)
              FROM unnest(con.conkey) WITH ORDINALITY k(att, ord)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = k.att)
      FROM pg_constraint con
      JOIN pg_class c ON c.oid = con.conrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE con.contype = 'c' AND n.nspname = %s AND c.relname = ANY(%s)
"""

#: A domain's own CHECKs, keyed by schema-qualified name.
_DOMAIN_CHECKS = """
    SELECT n.nspname || '.' || t.typname, pg_get_constraintdef(c.oid)
      FROM pg_constraint c
      JOIN pg_type t ON t.oid = c.contypid
      JOIN pg_namespace n ON n.oid = t.typnamespace
     WHERE c.contype = 'c' AND n.nspname = 'platform_ref'
"""


def derive_field_constraints(
    conn: psycopg.Connection[tuple[object, ...]], silver_schema: str
) -> tuple[list[FieldConstraint], list[SchemaRule]]:
    """Read one tenant's Silver DDL and project it onto the published fields.

    ANY provisioned tenant will do, and that is not a shortcut: tenant
    migrations are templated (`src/migrate/runner.py`), so every tenant's
    Silver schema is the same DDL with a different schema name. Reading one is
    reading all of them, and `make migrate`'s drift check is what keeps that
    true. The caller passes the schema explicitly rather than this module
    picking one, so a generator and a conformance gate can each say which
    tenant they meant.
    """
    fields = conn.execute(_PUBLISHED_FIELDS).fetchall()

    tables: set[str] = set()
    for _code, _scope, _name, table in fields:
        tables.add(str(table))
        tables.add(f"{table}{'_line'}")
    table_list = sorted(tables)

    facts: dict[tuple[str, str], tuple[object, ...]] = {
        (str(r[0]), str(r[1])): r[2:]
        for r in conn.execute(_COLUMN_FACTS, (silver_schema, table_list)).fetchall()
    }
    domain_checks: dict[str, str] = {
        str(r[0]): str(r[1]) for r in conn.execute(_DOMAIN_CHECKS).fetchall()
    }

    checks: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {}
    for relname, conname, expr, cols in conn.execute(
        _TABLE_CHECKS, (silver_schema, table_list)
    ).fetchall():
        checks.setdefault(str(relname), []).append(
            (str(conname), str(expr), tuple(str(c) for c in cast("list[str]", cols or [])))
        )

    # Which (table, column) pairs a tenant actually supplies. A constraint is
    # published only if EVERY column it touches is in here — see the module
    # docstring on envelope exclusion.
    published: set[tuple[str, str]] = set()
    field_table: dict[tuple[str, str, str], str] = {}
    for code, scope, name, table in fields:
        rel = f"{table}_line" if scope == _LINE_SCOPE else str(table)
        published.add((rel, str(name)))
        field_table[(str(code), str(scope), str(name))] = rel

    constraints: list[FieldConstraint] = []
    seen_rules: set[tuple[str, str]] = set()
    rules: list[SchemaRule] = []

    for code, scope, name, _table in fields:
        code, scope, name = str(code), str(scope), str(name)
        rel = field_table[(code, scope, name)]
        fact = facts.get((rel, name))
        if fact is None:
            # A published field with no column of that name. Not silently
            # skipped anywhere else either — `test_register_specs.py`'s DDL
            # gate already fails on it — so this stays an honest empty rather
            # than a second, weaker place the same drift could hide.
            constraints.append(FieldConstraint(code, scope, name))
            continue

        domain, max_len, precision, scale, is_int = fact
        # psycopg types a heterogeneous row as object; these five come
        # from known catalog columns, so the casts assert what the query
        # already guarantees rather than papering over an unknown.
        max_len = cast("int | None", max_len)
        precision = cast("int | None", precision)
        scale = cast("int | None", scale)
        shape = _Shape()

        if domain is not None and (dc := domain_checks.get(str(domain))):
            shape = _merge(shape, parse_check(dc, subject="VALUE", is_integer=bool(is_int)))

        for conname, expr, cols in checks.get(rel, ()):
            if cols != (name,):
                continue
            parsed = parse_check(expr, subject=name, is_integer=bool(is_int))
            if parsed is None:
                if (rel, conname) not in seen_rules:
                    seen_rules.add((rel, conname))
                    rules.append(
                        SchemaRule(code, conname, _strip_parens(_normalise(expr)), cols)
                    )
                continue
            shape = _merge(shape, parsed)

        constraints.append(
            FieldConstraint(
                doc_type_code=code, scope=scope, field_name=name,
                sql_domain=str(domain) if domain is not None else None,
                pattern=shape.pattern,
                allowed_values=shape.allowed_values,
                min_value=shape.min_value,
                max_value=shape.max_value,
                max_length=int(max_len) if max_len is not None else None,
                numeric_precision=int(precision) if precision is not None else None,
                numeric_scale=int(scale) if scale is not None else None,
            )
        )

    # Multi-column checks, once per (type, constraint). Every column must be
    # one the tenant supplies: `valid_to > valid_from` is real and is not
    # theirs to satisfy.
    for code, _scope, _name, table in fields:
        code = str(code)
        for rel in (str(table), f"{table}_line"):
            for conname, expr, cols in checks.get(rel, ()):
                if len(cols) < 2 or (code, conname) in seen_rules:
                    continue
                if not all((rel, c) in published for c in cols):
                    continue
                seen_rules.add((code, conname))
                rules.append(SchemaRule(code, conname, _strip_parens(_normalise(expr)), cols))

    return constraints, sorted(rules, key=lambda r: (r.doc_type_code, r.constraint_name))


def _merge(base: _Shape, other: _Shape | None) -> _Shape:
    """Later constraints refine, never overwrite with None.

    A column can be constrained twice — by its domain AND by a table CHECK —
    and both are real. Nothing in the current schema does both to the same
    facet, so there is no conflict rule here on purpose: inventing one (last
    wins? narrowest wins?) would be a decision with no case to justify it.
    """
    if other is None:
        return base
    return _Shape(
        pattern=other.pattern or base.pattern,
        allowed_values=other.allowed_values or base.allowed_values,
        min_value=base.min_value if other.min_value is None else other.min_value,
        max_value=base.max_value if other.max_value is None else other.max_value,
    )
