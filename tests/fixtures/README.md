# Fixtures

Two kinds of file live here, and the distinction is the whole point.

## `*_handwritten.csv` — written by hand, never generated

`scripts/gen_mock_erp.py` reads the same `field_contract` that
`src/silver/validate.py` enforces. So a **wrong contract produces a file that
validates perfectly**: the generator and the validator are wrong in the same
direction, and a suite built only on generated fixtures would pass with an
entire document type mis-specified.

These files were typed out against what the document actually looks like, not
against the contract. When one of them disagrees with the contract, the
disagreement is the finding.

**Do not regenerate a hand-written fixture to make a test pass.** If a
hand-written fixture stops validating, the candidates are, in order: the
contract is wrong, the validator is wrong, the fixture is wrong. Establish which
before editing anything.

## `*_rejected.csv` — hand-written, and must NOT validate

Negative fixtures, each isolating one rule. A validator that accepts everything
passes every positive test ever written, so these are the ones that give the
positives meaning. Each file's name says which rule it violates, and the test
asserts the *reason* — not merely that it failed.

## The typed-table fixtures

`sales_register_typed_handwritten.csv` and its two companions are written
against the **typed** ERP contract (`src/silver/sales_register.py`), not against
the archetype-1 `field_contract`. Same invoices as
`sales_register_handwritten.csv`, plus the header totals the archetype table had
nowhere to put.

The generator argument above does not apply to these — nothing generates them,
and `src/silver/sales_register.py` names its columns directly rather than
reading a contract. What still applies is the rule about not editing one to make
a test pass.

Two of them exist to pin decisions that are easy to reverse by accident:

  * **`sales_register_totals_disagree_handwritten.csv` must LOAD, not reject.**
    Its header claims 100000.00 taxable against a single 90000.00 line. That
    disagreement is a *finding* — `TYPED-TABLES-PLAN.md` §6 is the reason
    header and line are separate tables at all. Anyone who "fixes" the schema by
    restoring `CHECK (total_value = taxable + tax)` destroys the evidence and
    reports the file as unparseable. This fixture fails the moment they do.

  * **`sales_register_header_disagreement_rejected.csv` must REJECT.** Its two
    rows carry the same invoice number and different `place_of_supply`. There is
    no defensible way to pick a winner, so taking the first row's value would
    silently discard one of two contradictory claims.

## Generated fixtures

Produced on demand inside the tests rather than committed, so they cannot drift
from the generator. To eyeball one:

    python scripts/gen_mock_erp.py SALES_REGISTER --docs 3 --lines 2

## Real documents

None yet. When real client exports arrive, they belong here as
`*_real_redacted.csv` and should *replace* the hand-written fixture for that
type — a real file is better evidence than a careful guess about one.

## The flat A1 register fixtures

`purchase_register_typed_handwritten.csv`, `bank_statement_outward_handwritten.csv`
and `trial_balance_handwritten.csv` are written against `src/silver/registers/`,
which is spec-driven — so the generator argument at the top of this file applies
to it with full force. `tests/conformance/test_registers.py` synthesises a row
from the spec for its 23-way sweep, and **a wrong spec would produce a file that
loads perfectly**.

Two things stop that from being a hole:

  * `tests/conformance/test_register_specs.py` compares every spec against the
    live DDL — column set, order, required flags, kinds, period column, unique
    index. The database, which no test wrote, is the judge.

  * These three fixtures were typed against what the document actually looks
    like. Each carries something generation cannot produce:

    - **`purchase_register_typed_handwritten.csv`** — row 4 is a cash purchase
      from an unregistered supplier: no GSTIN, `rcm_flag=true`. Its natural key
      therefore contains a NULL, which `=` never matches. Row 3 is a TDS
      deductor, whose GSTIN carries 'D' at position 14 rather than 'Z'.
    - **`bank_statement_outward_handwritten.csv`** — two byte-identical ECS
      debits on 2026-04-06. A content-keyed table cannot tell two real payments
      from an export bug, so one is kept and the drop is COUNTED
      (`collapsed_duplicates`). This fixture is what makes that count non-zero.
    - **`trial_balance_handwritten.csv`** — an `fy`-keyed register with negative
      closing balances on the credit side. `money_inr` is signed on purpose.

  * **`purchase_register_bad_types_rejected.csv` must REJECT, with four distinct
    reasons** — a DD-MM-YYYY date, an Indian-format `2,40,000.00`, a blank
    required `invoice_no`, and `rcm_flag=perhaps`. All four are reported from one
    pass; a loader that stops at the first would send the file back four times.
