# Handoff — Category B (GST portal & government data)

Written 2026-08-19 to open a session dedicated to Category B. Nothing in this
document is built yet. Read `CLAUDE.md` first; this only covers B.

Steve is supplying sample data for Category B. This document records what is
pending, what each pending type actually costs, and the three decisions that
must be made before any of it is built — so the new session starts from here
rather than re-deriving it.

---

## 1. Where B stands

29 of the registry's 33 Category B types have no `dispatch_mechanism` — i.e.
nothing routes them from Bronze to Silver. **All 12 remaining MANDATORY types
in the entire 128-type registry are in this category.** Category A is done
except `A1.20`; Category D is 13 conditional types, all cheaper than B.

The 29 are not one job. They split cleanly by archetype, and the split is the
single most useful fact for planning:

| Archetype | Count | Target Silver table | Status |
|---|---|---|---|
| 5 — filed returns & portal state | **15** | *none — never built* | **Needs a new table design** |
| 6 — proceeding events | 6 | `proceeding_event` | **Table already exists** |
| 7 — entity & counterparty master | 4 | `entity_master_record` | **Table already exists** |
| 3 — entitlement instruments | 3 | `entitlement_instrument` | **Table already exists** |
| 2 — periodic registers | 1 | one new per-type register table | Existing mechanism |

**14 of the 29 need no new table at all.** Verified directly against
`information_schema` on the live cluster (`t_acme_silver`), not inferred:
`proceeding_event`, `entity_master_record` and `entitlement_instrument` were
all built in the fourth session and are sitting empty of these types.

### The cheap 14 — registry rows, not code

`B2.01`–`B2.03`, `B2.06`, `B2.07`, `B3.01`–`B3.06`, `B6.02`, `B6.03`.

If these arrive as PDFs with a `Label: Value` shape, each one is a
`registry/document_types.csv` row — an `extraction_spec` cell plus
`dispatch_mechanism=PDF_EXTRACTION` — and **zero Python**. That is the
mechanism the fifth and sixth sessions built precisely so this would be true;
the acceptance test for it (an archetype-3 type served by a live registry
UPDATE with no process restart) is recorded in `CLAUDE.md`'s fifth-session
entry. If any of these needs a code branch, stop — the archetype abstraction
has failed and that is the bug, not the document.

`B6.01 DGFT_EBRC` (archetype 2) is one new register table + one `RegisterSpec`
+ a CSV row, following tenant migration `020`'s three-table precedent exactly.

### The expensive 15 — archetype 5 has no table

`B1.01`–`B1.11`, `B2.04`, `B2.05`, `B3.07`, `B5.02`.

`ARCHITECTURE.md` §6 sizes archetype 5 at ~15 types and describes it as
"Filed returns & portal state — Structured (Stream A)". No table was ever
built for it. This is the real work in Category B, and it holds 7 of the 12
mandatory types (`GSTR_1`, `GSTR_1_ARN`, `GSTR_2B`,
`GSTR_2B_AMENDMENT_HISTORY`, `GSTR_3B`, `GSTR_9`, `GSTR_9C`).

---

## 2. Three decisions to make before building

**UPDATE 2026-08-19, same day, later session: all three are DECIDED.** See
`CLAUDE.md`'s tenth-session "Current state" entry for the full record — this
section is kept for its reasoning, not as an open question anymore.

- **D1**: typed columns, except named narrative fields. **Not** a shared
  `filed_return` header table across GSTR types (the option floated below,
  and this document's own original recommendation) — `TYPED-TABLES-PLAN.md`
  §8 already rejects exactly that shape, caught before any DDL was written.
  Each return type gets its own table set. `GSTR_2B`'s is built
  (`migrations/tenant/026_gstr_2b.sql`, `src/silver/gstn_returns/gstr2b.py`).
- **D2**: upload-first, poller deferred. Confirmed, unchanged from the
  recommendation below.
- **D3**: yes, a new dispatch mechanism (`GSTN_JSON_PROMOTE`, shared
  migration `033`), bespoke Python per return type — not a declarative spec
  grammar, mirroring the archetype-2 `register_types.py` precedent.

These are Steve's calls, not mine. Each changes the shape of the work.

### D1. What shape does archetype 5 take?

`TYPED-TABLES-PLAN.md` is the design of record and says: for `STRUCTURED`
types, **one typed table per document type**, not an archetype table with a
`jsonb` bag. All 15 archetype-5 types are `STRUCTURED`. Read literally, that
means 15 new tables.

The complication that makes this a real question rather than a lookup: a
GSTR-1 is not flat. It is a filing containing many sections (B2B, B2CL,
B2CS, CDNR, exports, HSN summary, documents issued), each with a different
column set — closer to `sales_register`'s header/line split than to the 23
flat registers. A GSTR-3B is a summary form with fixed boxes. A GSTR-9C is a
reconciliation statement. These three do not share a shape, so "one archetype
5 table" would repeat exactly the mistake that got archetype 2 withdrawn
(`CLAUDE.md`: "`PERIODIC` is a cadence, not a shape... It was cut on the
wrong axis").

Options worth weighing when the samples arrive, not before:
- one typed table per return type, each with its own line table where the
  return has sections (most faithful to `TYPED-TABLES-PLAN.md`, most tables);
- a `filed_return` header table carrying the filing-level facts every return
  shares (GSTIN, period, ARN, filing date, status) plus per-return section
  tables hanging off it (fewer tables, one real shared concept, and `ARN`
  genuinely is common to all of them);
- something narrower for the four non-return archetype-5 types (`B2.04`,
  `B2.05`, `B3.07`, `B5.02`), which are portal *state*, not filings, and may
  not belong with the returns at all.

**Do not pick one from the document titles.** Pick it from the sample JSON.

### D2. Upload now, poller later?

`ARCHITECTURE.md` §7 calls Stream A "a materially different machine" — a
scheduled poller with Vault credentials, rate limits, downtime handling and
ARN tracking — and phases it as a separate build (§ phase 3).

But nothing forces the two to ship together. A client can export their own
filed GSTR-2B/3B JSON from the portal and upload it through the ingestion
surface that already exists. That path reconciles real data months before any
credential handling is written, and the poller then becomes a second *caller*
of the same dispatch mechanism rather than new logic — the same relationship
the deferred `load_trigger` worker has to `dispatch_load` today.

Recommendation: **build upload-first.** Defer the poller. But confirm, because
if the intent is that INAFIN always fetches returns itself, the upload path is
throwaway work.

### D3. Does archetype 5 need a new dispatch mechanism?

The four existing mechanisms are `PDF_EXTRACTION`, `SALES_REGISTER`,
`REGISTER_LOADER`, `ARCHETYPE1_PROMOTE`. GSTN JSON is none of them — it is
nested JSON against a published API schema, not a flat CSV/NDJSON row stream
and not a PDF text layer.

So this is the one case the sixth session named as legitimate: *"Adding a
genuinely new mechanism still needs one new branch in
`src/dispatch/router.py`, once — unavoidable, but a one-time cost per
mechanism, not per document type."* Expect to add roughly one
`GSTN_JSON`-style mechanism, and then every subsequent return type must be a
CSV row. If return #2 needs a second branch, the mechanism was drawn wrong.

Note the existing NDJSON support is **not** this. NDJSON is one flat record
per line; a GSTR-2B is a single deeply nested document.

---

## 2b. THE SAMPLES HAVE ARRIVED — read this before section 3

`reference/B-Documents/` landed 2026-08-19, while this document was being
written. **146 data files, every ref from B1.01 to B6.03**, plus a
`README.md`, a `MANIFEST.json`, an `ANOMALY_KEY.json` and the `_generator/`
that produced them. Section 3 below was written before they arrived and is
kept only for its reasoning about fixtures; the concrete asks in it are
answered.

Mock taxpayer: VARDHMAN PRECISION INDUSTRIES PVT LTD, FY 2024-25, monthly
filer, 4 GSTINs (KA primary, MH branch, TN depot registered mid-year, KA ISD).
All GSTINs carry valid check digits. The documents are deliberately
**cross-tied** — GSTR-1 → 3B → 9 → 9C, GSTR-2B → 3B Table 4, export invoice →
shipping bill → EGM → eBRC, invoice → IRN → e-way bill, marketplace turnover →
GSTR-8 — so a reconciliation that works should notice when one is broken.
`ANOMALY_KEY.json` names the anomalies seeded on purpose. **That file is the
expected-results oracle for reconciliation tests — do not read it into the
loader path, and do not "fix" the data it describes.**

### Real shapes, read from the files (not assumed)

- **GSTR-2B** — `data.docdata.{b2b,cdnr,impg,isd}`, each a list **grouped by
  supplier** (`ctin`, `trdnm`, `supprd`, `supfileddt`) with a nested `inv`
  list under each, plus a separate `data.itcsumm.{itcavl,itcnotavl}` summary
  block. Three levels of nesting. This is the hardest shape in the set and the
  reason D3 exists.
- **GSTR-3B** — flat-ish nested dicts of fixed boxes (`sup_details.osup_det`,
  `itc_elg.itc_avl[]` keyed by `ty`, `inter_sup`, `intr_ltfee`), plus `arn` and
  `filing_date`. No supplier grouping. A genuinely different shape from 2B.
- **GSTR-1** — `b2b[]` grouped by `ctin` → `inv[]` → `itms[]` (three levels),
  alongside `b2cs[]` which is flat rate-wise summary rows, plus `b2cl`, `cdnr`,
  `exp`. So GSTR-1 is *both* shapes at once.
- **GSTR-9C** — not a return at all in shape: named-key reconciliation tables
  (`table5_...A_turnover_as_per_audited_financials` … `L_other_adjustments`),
  a `table6_...[]` reasons list, `table12_...` ITC recon, and an `auditor`
  block. Closest existing analogue is `financial_statement_extract`.

**This settles D1 as far as it can be settled from data: "one archetype-5
table" is definitively wrong.** 2B, 3B, 1 and 9C do not share a column set.
The filing-level facts they *do* share are exactly `gstin`, period, `arn`,
`filing_date`, `filing_status` — which is a real shared header concept and
argues for the second option in D1 (a `filed_return` header + per-return
section tables), not the first or third.

### Flat CSV alternatives exist for most types — this is the bigger finding

Nearly every B2, B3, B5 and B6 type ships as **CSV**, and several B1 types
ship as *both* per-period JSON and one flat all-lines CSV (`B1.03` has
`GSTR2B_all_lines_FY2024-25.csv` beside its 24 JSON files; `B1.05` has
`GSTR3B_summary_FY2024-25.csv`; `B1.02`, `B1.04`, `B1.10`, `B1.11` are
CSV-first).

That matters a lot for scope: the existing `REGISTER_LOADER` mechanism already
consumes flat CSV. So a large part of Category B may be reachable with **no
new dispatch mechanism at all** — per-type tables plus `RegisterSpec` entries,
exactly the 23-register pattern. The nested JSON path would then be needed
only where the nesting carries information the flat CSV drops.

**Do not assume the CSVs are lossless projections of the JSON.** Check, per
type, whether the flat file preserves supplier grouping, ITC-availability
flags and the summary blocks. If it does, prefer it and skip the JSON parser
entirely for that type; if it does not, the JSON is the source of record and
the CSV is a convenience. This check is the first task of the build session
and it decides how much of D3 is even needed.

### B4 and B5 are in the box but were not in the pending list

The samples include `B4_EInvoice_and_EWayBill` (B4.01 IRN register, B4.02
e-way bill register, B4.03/B4.04 threshold histories) and
`B5_ICEGATE_Customs` (B5.01 shipping bills, B5.02 EGM, B5.03 BoE data).
Only `B5.02` is on the pending list — the rest already have a
`dispatch_mechanism`, and **B4.03/B4.04 are the two `stream=CORPUS` rows that
belong to `inafin-gst-corpus`, not to this repo** (`registry/README.md` §2).
Check each against the registry before building anything for them; some are
already done and some are not ours.

---

## 3. What I need in the samples

Ranked by what unblocks the most work.

1. **Real GSTN JSON downloads** for `GSTR_2B` and `GSTR_3B` first — sanitised,
   but structurally intact (nesting preserved, all sections present, at least
   one row in each section that a real filing would populate). These two
   settle D1 and D3 between them: 2B is the nested multi-section case, 3B the
   fixed-box summary case. Add `GSTR_1` next, since it is the largest.
2. **`GSTR_9` / `GSTR_9C`** — annual, and 9C is a reconciliation statement
   that may not resemble the others at all.
3. **PDFs for the cheap 14** — one specimen each. These need no design
   decision; each is a registry row once I can see the label layout. If the
   samples arrive PDF-shaped, I can land most of these while D1 is still open.
4. **A statement of which are JSON vs PDF vs both.** Several B2/B3 types could
   plausibly arrive either way (a REG-06 certificate is a PDF; GSTIN status is
   an API response). Which one a type arrives as decides its archetype
   handling, and guessing it is how `026`-`028` went wrong once already.

### On synthetic fixtures — the one place they are legitimate

The repo's standing rule is: *never mock a document layout with no ground
truth.* That rule is why Category D is blocked and why OCR went unbuilt for
three sessions.

**Archetype 5 is the documented exception.** GSTN publishes the API schema, so
the export shape is a specified contract rather than an observed layout — the
same asymmetry that makes synthetic ERP CSVs legitimate (`CLAUDE.md`,
"Mock data: generated *and* hand-written, on purpose"). Synthetic GSTR JSON
built *against the published schema* has real fidelity.

The trap that comes with it, already burned once in this repo: a fixture
generated from the same spec the validator enforces will validate perfectly
even when the spec is wrong. So at least one **hand-written** fixture per
return type, typed against a real filing, is required — not generated. See
`tests/fixtures/README.md`.

---

## 4. Suggested order — samples are in, so this is the actual plan

0. **First, the CSV-vs-JSON losslessness check described in §2b.** It decides
   whether a new dispatch mechanism is needed at all, and it is an hour of
   reading, not a build. Do it before writing any migration.

1. `GSTR_2B` end to end — table(s), mechanism, loader, tests. It is mandatory,
   it is the hardest shape, and it is the primary ITC source for every B-series
   reconciliation section. Getting it right sets the pattern for the other 14.
2. `GSTR_3B` and `GSTR_1` onto that pattern — this is where it becomes clear
   whether D1 was decided correctly. If either needs a new mechanism branch,
   revisit before continuing.
3. The cheap 14, in bulk, as registry rows.
4. `B6.01 DGFT_EBRC` as a register table.
5. The remaining archetype-5 stragglers (`B2.04`, `B2.05`, `B3.07`, `B5.02`).

Keep `docker compose down -v` off the table throughout — the standing rule in
`CLAUDE.md` (shared dev server) applies to this work like any other. Verify
with targeted DDL and `information_schema` queries against the live cluster.
