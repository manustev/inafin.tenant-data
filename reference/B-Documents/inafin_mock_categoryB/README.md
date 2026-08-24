# INAFIN — Category B Mock Data (GST Portal & Government Data)

Synthetic stand-in for **Category B — GST Portal and Government Data** of the INAFIN GST Audit
Reconciliation Framework (Doc 4, Section 2). Every B-ref from **B1.01 to B6.03** has data.
Use it to build and test ingestion + reconciliation logic until real GSTN / ICEGATE / DGFT
access is available.

Everything here is fabricated. No real taxpayer, GSTIN, ARN, IRN, shipping bill or notice
number is used.

---

## 1. The mock taxpayer

| Item | Value |
|---|---|
| Legal name | VARDHMAN PRECISION INDUSTRIES PRIVATE LIMITED |
| PAN / CIN | AABCV1234K / U28999KA2011PTC061234 |
| Profile | Mid-size precision engineering manufacturer, exporter (LUT), SEZ supplier, marketplace seller, importer |
| Financial year | 2024-25 (Apr 2024 – Mar 2025), monthly filer |
| Karnataka GSTIN (primary, HO + factory) | `29AABCV1234K1Z9` |
| Maharashtra GSTIN (branch) | `27AABCV1234K1ZD` |
| Tamil Nadu GSTIN (depot, **registered 12-08-2024**) | `33AABCV1234K1ZK` |
| ISD GSTIN (Karnataka) | `29AABCV1234K2Z8` |
| Annual outward turnover (all GSTINs) | ≈ ₹21.9 crore, of which ≈ 29% zero-rated (export under LUT + SEZ) |

All GSTINs — taxpayer, 9 customers, 12 suppliers, 2 marketplace operators — carry a **valid
GSTIN check digit**, so format/checksum validators will pass.

The set is deliberately built so the documents **tie to each other**: GSTR-1 → GSTR-3B → GSTR-9 →
GSTR-9C, GSTR-2B → GSTR-3B Table 4, export invoice → shipping bill → EGM → eBRC, invoice → IRN →
e-way bill, marketplace turnover → GSTR-8. Break one and your reconciliation should notice.

---

## 2. What's in the box (146 data files)

| Ref | Content | Files | Format |
|---|---|---|---|
| B1.01 | GSTR-1, all tables (B2B, B2CL, B2CS, CDNR, EXP/6A, AT, HSN, doc issue) — 31 monthly returns across 3 GSTINs | 31 JSON + 1 flat CSV of every line | JSON, CSV |
| B1.02 | ARN + filing date register for every GSTR-1 / 3B / 6 / 9 filing, with due date, delay days, late fee | 1 | CSV |
| B1.03 | GSTR-2B — invoice-level ITC incl. IMPG (imports) and ISD credits, ITC-available flags | 24 JSON + 1 flat CSV | JSON, CSV |
| B1.04 | GSTR-2B amendment history — supplier amendments hitting an earlier period's availed ITC | 2 | CSV, JSON |
| B1.05 | GSTR-3B — Tables 3.1, 3.2, 4A/4B/4D, 5, 6 | 31 JSON + 1 summary CSV | JSON, CSV |
| B1.06 | GSTR-9 annual return — Tables 4, 5, 6, 8, 9, 11, 14 | 3 | JSON |
| B1.07 | GSTR-9C — Table 5 turnover bridge (A→R), Table 6 reasons, Table 12 ITC recon, auditor block | 1 | JSON |
| B1.08 | GSTR-2A historical (FY 2018-19, pre-2B) for Forensic Mode | 3 | JSON |
| B1.09 | GSTR-6 ISD returns — inward common services + distribution to 3 GSTINs, Rule 39 turnover basis | 12 | JSON |
| B1.10 | GSTR-7 — TDS u/s 51 deducted by a government customer, invoice-wise + GSTR-7A cert nos. | 2 | CSV, JSON |
| B1.11 | GSTR-8 — TCS by Amazon and Flipkart, POS-wise, monthly | 2 | CSV, JSON |
| B2.01 | REG-06 for all 4 GSTINs (incl. annexures, signatories, jurisdiction) | JSON + CSV + **PDF** | JSON, CSV, PDF |
| B2.02 / B2.03 | Customer and supplier registration status (type, active/cancelled, cancellation date) | 2 | CSV |
| B2.04 | Registration amendment history (REG-14 core/non-core) | 1 | CSV |
| B2.05 | Composition CMP-02 opt-in / CMP-04 opt-out with effective dates | 1 | CSV |
| B2.06 | ISD registration certificate, effective 01-10-2023 | 1 | JSON |
| B2.07 | **GSTIN status at date** — the point-in-time lookup your code will call most | 1 | CSV |
| B3.01 | Open SCN register (DRC-01 ×2, ASMT-10 ×1) with stage, amounts, officer | CSV + JSON + **PDF** | CSV, JSON, PDF |
| B3.02 | DRC-01B (Rule 88C GSTR-1 vs 3B intimation) — amounts computed from the actual Sep-24 pair | JSON + **PDF** | JSON, PDF |
| B3.03 | DRC-03 voluntary payments (one standalone, one against SCN) | 1 | CSV |
| B3.04 | Amnesty / settlement order (Sec 128A SPL-02) | 1 | CSV |
| B3.05 | Court stay order (HC writ, pre-deposit condition) | 1 | CSV |
| B3.06 | Personal hearing + adjudication (DRC-07) records | 1 | CSV |
| B3.07 | RFD-01 refund applications with RFD-02/03/04/06 status, deficiency memo, days pending | 1 | CSV |
| B4.01 | IRN register (306 IRNs, SHA-256 style, ack no/date, one cancelled IRN) + a full e-invoice JSON payload | 2 | CSV, JSON |
| B4.02 | E-way bill outward register (207 EWBs, transporter, vehicle, distance, validity, cancelled/extended) | 1 | CSV |
| B4.03 | E-way bill threshold history — inter-state + state-wise intra-state rollout and ₹1 lakh states | 1 | CSV |
| B4.04 | E-invoice threshold history — all six AATO events, Oct-2020 → Aug-2023, with notification refs | 1 | CSV |
| B5.01 / B5.02 | ICEGATE shipping bills (FOB, FC, exchange rate, LUT) + EGM status and GSTN transmission | 2 | CSV |
| B5.03 | Bill of Entry data — assessable value, BCD, SWS, IGST, out-of-charge, 2B reflection flag | 1 | CSV |
| B6.01 | DGFT eBRC register — realisation per shipping bill, days from invoice, status | 1 | CSV |
| B6.02 | IEC registry — branches, RCMC, GSTIN linkage | 1 | JSON |
| B6.03 | Export obligation status per EPCG / Advance Authorisation licence | 1 | CSV |

Plus `ANOMALY_KEY.json`, `MANIFEST.json` (every file + byte size), and `_generator/` (the Python
that produced all of it — change `SEED`, the monthly targets, or the FY and regenerate).

---

## 3. Anomaly key — 19 seeded defects

`ANOMALY_KEY.json` is your expected-results fixture: each entry names the documents involved,
the period, and the check that should fire. Summary:

| ID | Where | Defect |
|---|---|---|
| MOCK-01 | B1.03/04/05 | Supplier amends a Nov-24 invoice in the Jan-25 2B, cutting taxable value 28% after ITC was availed |
| MOCK-02 | B2.02/07 | Outward invoices raised on a customer whose GSTIN was cancelled w.e.f. 30-09-2024 |
| MOCK-03 | B2.03/B1.03 | ITC availed on a supplier cancelled w.e.f. 31-12-2024 |
| MOCK-04 | B5.02 | Shipping bill with EGM not filed — refund blocked |
| MOCK-05 | B5.01 | Shipping bill FOB exceeds GSTR-1 Table 6A value by 6.2% |
| MOCK-06 | B6.01 | Export invoice with no eBRC/FIRC — Rule 96A 12-month realisation breach |
| MOCK-07 | B5.01 | GSTR-1 Table 6A entry with no shipping bill number |
| MOCK-08 | B1.01/05, B3.02 | Sep-24 GSTR-3B understates outward supply vs GSTR-1 by ₹44.6 lakh taxable — the DRC-01B in B3.02 is issued on exactly this gap |
| MOCK-09 | B1.03/05 | Oct-24 ITC availed at 80% of 2B (deferred availment → recovery opportunity, Circular 183/15/2022) |
| MOCK-10 | B1.03/05 | Jan-25 CGST/SGST ITC availed at 106% of 2B (Sec 16(2)(aa) breach) |
| MOCK-11 | B1.02 | Jul-24 GSTR-1 and 3B filed late — late fee + Sec 50 interest |
| MOCK-12 | B1.09 | ISD distributed credit to the TN GSTIN before its 12-08-2024 registration date |
| MOCK-13 | B1.11 | Amazon GSTR-8 Dec-24 TCS base 9% above seller-declared marketplace turnover |
| MOCK-14 | B3.07 | RFD-01 with deficiency memo past the 60-day processing window |
| MOCK-15 | B4.01 | Three invoices above the e-invoice threshold with no IRN (Rule 48(5)) |
| MOCK-16 | B4.02 | Two goods invoices > ₹50,000 with no e-way bill |
| MOCK-17 | B5.03 | BoE with IGST paid at customs not reflected in 2B (IMPG) |
| MOCK-18 | B6.02 | IEC branch 03 (Chennai) has no GSTIN mapped — blocks ICEGATE-GSTN flow |
| MOCK-19 | B6.03 | Advance Authorisation EO period expired with 25% shortfall |

Also present as *legitimate* records that must **not** be re-flagged as open anomalies:
an SCN under High Court stay (B3.05), a DRC-03 part payment against that SCN (B3.03), and a
Section 128A settlement closing FY 2017-18 to 2019-20 (B3.04).

---

## 4. Schema notes

- **GSTR-1 / GSTR-2B / GSTR-3B** follow GSTN public API field naming (`ctin`, `inum`, `idt`,
  `txval`, `iamt`, `camt`, `samt`, `csamt`, `itcavl`, `sup_details`, `itc_elg` …) so your parser
  can be written against the real shape. Dates are `DD-MM-YYYY`, periods are `MMYYYY`.
- The payload is the **decrypted, unwrapped** body. Live GSTN API responses add an envelope
  (`status_cd`, `rek`, `data` base64/AES) plus auth headers — your transport layer will differ.
- **GSTR-9 / 9C** are given as readable, table-keyed JSON rather than the government's terse
  table-code schema. If you're parsing filed 9/9C JSON directly, treat these as fixtures for
  business logic, not as schema samples.
- For datasets with **no public JSON standard** (registration status, notices, EWB/IRN registers,
  ICEGATE, DGFT) I used self-descriptive snake_case CSV. Remap to your canonical model.
- `mock_flag` / `mock_note` columns mark the seeded defects. Strip them if you want a clean feed.

---

## 5. Limits — what this data cannot do for you

Calling these out rather than letting them bite later:

1. **Nothing here validates against a real system.** ARNs, IRNs, ack numbers, EWB numbers,
   shipping bill numbers, BoE numbers, eBRC numbers and notice reference numbers are
   *structurally* plausible only. GSTIN check digits are genuinely computed; the rest are not
   verifiable and the GSTINs themselves don't exist on GSTN.
2. **The 3 PDFs are cosmetic.** REG-06, DRC-01B and DRC-01 are laid out like portal documents but
   are not DSC-signed and have no QR code. If you're testing PDF *parsing* against real portal
   downloads, expect different fonts, table geometry and a signature block. Real portal PDFs also
   vary by state and by year — I'd not build an extraction pipeline against these alone.
3. **B4.03 e-way bill threshold history is the weakest dataset.** Inter-state (₹50,000 from
   01-04-2018) is solid; the state-wise intra-state rollout dates and the ₹1 lakh states
   (Delhi, Maharashtra, Tamil Nadu, West Bengal, Rajasthan) are from memory and several have been
   amended since. Treat it as a schema sample, and populate the real corpus from state
   notifications before you rely on threshold-at-date logic. B4.04 (e-invoice) is reliable —
   all six AATO events with notification numbers.
4. **DRC-01B realism ceiling.** Rule 88C parameters are set by the Council and, at this taxpayer's
   scale (≈₹1.65 crore monthly turnover), a difference large enough to trip the notified threshold
   would swallow the whole month. The mock notice is issued on a ₹44.6 lakh taxable / ≈₹8 lakh tax
   gap; if your tests assert the actual threshold, scale the entity up in `_generator/core.py`.
5. **No GSTR-2A for FY 2019-20 onward** and only 3 sample months of FY 2018-19 — enough to test
   a pre-2B Forensic path, not enough for a full historical engagement.
6. **B3.05 / B3.06** (court orders, hearing and adjudication orders) are structured registers.
   Real ones arrive as scanned or text PDFs from the court registry and the officer, not from any
   API — there is no realistic structured feed to mock.
7. **Categories A, C and D are not covered.** In particular, the client-side sales/purchase
   registers (A1.01/A1.03) that these returns are supposed to be reconciled *against* don't exist
   yet — right now the GSTR-1 *is* the books. Say the word and I'll generate a matching Category A
   ERP set with its own controlled divergences (that's where the real reconciliation muscle gets
   tested), and a Category D marketplace set (Amazon MTR / Flipkart GTR / SSR / DRR / VRET) that
   ties to the GSTR-8 above.
8. Cess is zero throughout — no cess-bearing HSN is in the product master. Add one if you need
   compensation cess logic.

---

## 6. Regenerating

```bash
cd _generator && python3 run_all.py    # writes the whole tree
```

`core.py` holds the seed (`SEED = 20240401`), the entity master, monthly turnover targets and the
anomaly injector; `b1.py` and `b2_b6.py` are the emitters. Change the seed for a different but
equally consistent dataset, or the FY / turnover targets for a different client profile.
