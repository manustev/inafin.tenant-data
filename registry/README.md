# Document Type Registry — draft for review

`document_types.csv` is the tenant-side half of Doc 4 Section 2, resolved into
ingestible document types. **130 rows, one per register ref**, group counts
matching §2.1 exactly (A=74, B=35, D=21).

Category C (37 docs — Acts, Rules, notifications, circulars, HSN master) is
deliberately absent. It is platform regulatory corpus, not tenant data, and
belongs to `inafin-gst-corpus`.

Status: **draft. Not seeded, not migrated.** Review the decisions below first.

---

## Columns

| Column | Meaning |
|---|---|
| `ref` | Register ref, verbatim from Doc 4 §2 |
| `alias_refs` | Other refs describing the *same* document — see Duplicates |
| `doc_type_code` | Stable code. Becomes `platform_ref.universal_master(Document_Type)` |
| `obligation` | MANDATORY / CONDITIONAL — drives the T9 completeness gate |
| `mode` | BOTH / FORENSIC / OPERATIONAL — depends on ADR-008 |
| `stream` | How it arrives (§7). Not the same as category |
| `archetype` | 1–8 per ARCHITECTURE.md §6. Determines the Silver table |
| `silver_storage` | STRUCTURED / HYBRID / NONE — what Silver does with it |
| `refresh_cadence` | ONE_TIME / PERIODIC / CONTINUOUS — when to ask again |

### `stream` is not `category`

The register's category says *who holds* the document. The stream says *how we
get it*, which is what determines the connector. They diverge:

- **A — portal-authoritative.** Poller: scheduler, Vault credentials, rate
  limits, downtime handling, ARN tracking. 32 rows.
- **B — client-provided.** Upload or ERP connector. Client asserts the content.
  73 rows.
- **C — third-party-authoritative.** Marketplaces, AD banks, MCA, NCLT, courts.
  Neither client nor GSTN. Trust semantics differ from both. 23 rows.

`A1.20` is the clearest divergence: filed under Category A, but the marketplace
operator is authoritative, not the client. It is Stream C.

### `silver_storage`

Bronze is uniform — every row here produces one Object-Locked object plus one
ledger row regardless of type (§3.1). This column governs **Silver only**.

- `STRUCTURED` — fully normalised into its archetype table.
- `HYBRID` — blob remains the evidentiary record; a defined field set is
  extracted into the archetype table for querying. Every instrument, order,
  certificate and contract is Hybrid: the *document* is the evidence, the
  extracted `valid_from`/`valid_to`/`scope` is what the rules query.
- `NONE` — out of scope for this repo.

### `refresh_cadence`

Doc 4 §2: *"In Operational Mode, Mandatory documents are ingested at onboarding
and refreshed at configured intervals."* This is what drives that.

Read from the **platform's refresh perspective**, not the document's own
periodicity — an annual return is PERIODIC because we re-fetch it, and a LUT is
ONE_TIME even though it is issued per financial year, because a new LUT is a new
instrument row rather than a refresh of the old one.

| | | |
|---|---|---|
| `ONE_TIME` | 45 | Requested at onboarding; re-requested only on change or expiry |
| `PERIODIC` | 67 | Re-fetched or re-requested on a schedule |
| `CONTINUOUS` | 16 | Delta-loaded as transactions occur |

Mostly tracks archetype — 1 is continuous, 2/4/5/6 periodic, 3/7/8 one-time —
with the exceptions being the interesting part:

- **Instruments whose *status* must be current**, not merely held: DGFT export
  obligation status, IEC validity, SEZ APR, EOU annual compliance. Holding the
  instrument is one-time; its standing is periodic, and zero-rating depends on
  the standing.
- **Counterparty state**: supplier and customer registration status is polled,
  because the test is active-at-invoice-date, not active-now.
- **Per-transaction arrivals** that look like registers: FIRC, BRC, Form
  15CA/15CB, EGM status, eBRC arrive per invoice or per remittance.

NULL where `in_scope` is false — cadence is meaningless for a document this repo
does not ingest, and a default would be a fact nobody asserted.

---

## Decisions to review

### 1. Duplicates — the register counts four documents twice

| Canonical | Alias | Same document? |
|---|---|---|
| `D1.01`–`D1.07` | `A1.20` | Yes. Onboarding item 22 cites them together as one ask. |
| `B3.04` | `D5.07` | Yes. Amnesty / settlement order. |
| `B3.05` | `D5.06` | Yes. Court / stay order. |
| `D5.01` | `C4.03` | Yes — **the register says so itself**: C4.03's note reads "Stored in Group D5." |

Collapsed to one canonical row each with the other ref kept as `alias_refs`, so
either ref resolves. **130 refs → 125 distinct document types** after collapsing
and removing the two corpus rows.

This matters beyond tidiness: left as-is you build two ingestion paths for one
file, and the completeness gate demands the same document twice.

### 2. Two "Category B" rows are corpus, not tenant data

`B4.03` (E-Way Bill threshold history) and `B4.04` (e-invoice threshold history)
are both sourced "INAFIN Corpus" and both bitemporal reference data. They are
marked Mandatory, so they must not simply vanish — they are recorded here as
`stream=CORPUS`, `silver_storage=NONE`, and are an obligation on
`inafin-gst-corpus`, not on this repo.

### 3. Not duplicates, despite looking like it

- **`A5.14` (FIRC/BRC register) vs `D2.01`/`D2.02` (FIRC/BRC certificates).**
  A periodic register the client maintains, versus the bank-issued instruments
  it summarises. Different archetype, different stream, different trust. Both.
- **`A6.01` (Bill of Entry, client copy) vs `B5.03` (BoE data from ICEGATE).**
  Same underlying import, two independent sources. Keeping both is the *point* —
  it is a reconciliation pair, and collapsing it would destroy a check.
- **`B6.01` (DGFT eBRC) vs `D2.01` (bank FIRC).** The register is explicit that
  both are required for zero-rating defence.

### 4. Archetype 3 is a third of the register

33 of 125 rows are entitlement instruments — LUT, IEC, every SEZ/EOU/STPI
document, EPCG, AA, EODC, B-17, BLUT, AEO, SCOMET, the taxpayer's own AAR,
incentive approvals, entity-specific clarifications. All one table, all
answering one query:

> Was an active instrument of type T, covering HSN H, held by entity E on date D?

Archetype 2 (periodic registers) is another 28. **Those two archetypes are half
the register.** If the build starts anywhere else it is optimising the tail.

---

## Artefact types vs row types — surfaced by seeding

`universal_master.Document_Type` now holds **132** values: the registry's 127,
plus the 5 Phase 1 seeded before it existed (`PURCHASE_INVOICE`, `SALES_INVOICE`,
`CREDIT_NOTE`, `DEBIT_NOTE`, `PAYMENT_RECORD`).

Those 5 are not in the registry, and the reason is a real distinction the build
had not yet had to make:

- **Artefact type** — what arrives. `A1.03 PURCHASE_REGISTER` is one file.
- **Row type** — what comes out of it. A purchase register yields many purchase
  invoices.

`artefact_ledger.declared_document_type` should carry the *artefact* type. Phase 1
declares `PURCHASE_INVOICE`, which is the row type. It works, and every gate
passes, because the FK only requires the value to exist — but it means two
vocabularies describe overlapping things.

**DECIDED 2026-08-04 when Archetype 1 was built: the registry carries artefact
types only.** Row types belong to the archetype tables, expressed as
`(doc_type, direction)` — `PURCHASE_REGISTER` + `INWARD` carries everything
`PURCHASE_INVOICE` carried, in the vocabulary the register already uses. The
alternative, growing the registry to hold row types too, would mean every
archetype declaring which row types it emits and the T9 completeness gate
knowing that a request for one implies the other: two vocabularies describing
overlapping things, which is the thing this finding objected to.

The decision was forced rather than chosen. `transaction_document.doc_type`
carries the same CHECK-pinned composite FK to `document_type (doc_type_code,
archetype)` that `entitlement_instrument` does, so only a registry code can be
stored — `PURCHASE_INVOICE` is not one.

The 5 are **retired from use, not deleted.** Bronze is INSERT-only and existing
`artefact_ledger` rows reference them, so deletion would break history that must
stay resolvable. Their `universal_master` descriptions say so, and
`tests/conformance/test_transaction.py::test_retired_row_type_is_refused` stops
a new one being emitted. That guard is a test and not a foreign key precisely
because the FK still has to accept them for the historical rows.

---

## Open questions for sign-off

1. **`mode` depends on ADR-008 — decided 2026-08-03: Operational only.** 8
   in-scope rows are FORENSIC-only and all 8 are CONDITIONAL, so deferring the
   document types is free. Deferring `engagement_id` is not. See "Deferring
   Forensic Mode" in `TODO.md`.
2. **`A5.06` (SEZ Notification)** is a government notification listed as
   client-provided. Arguably corpus. Left as Stream B pending a call.
3. **`obligation` is engagement-invariant here.** The register implies Mandatory
   can vary by client profile (items 19–25 of the onboarding request are
   profile-gated). If that is right, obligation belongs on the engagement, not
   the document type.
