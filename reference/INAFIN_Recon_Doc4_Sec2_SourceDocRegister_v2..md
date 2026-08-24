# Section 2 — Source Document Register

This is the consolidated reference of every source document required to run the full INAFIN GST Audit Reconciliation Framework across sections A1 through A10 (Output Tax) and B1 through B8 (Input Tax Credit). It also covers the entity

entitlement and allied law instruments that establish the legal right to make specific GST claims.

Four top-level categories: (A) Client-Provided Documents — data and instruments held by and obtained from the client. (B) GST Portal and Government Data — returns, statements, and official records retrieved from GSTN and allied

government portals. (C) Platform Regulatory Corpus — INAFIN-resident reference data, not client-provided. (D) External Third Party — data held by parties other than the client, GSTN, or INAFIN.

Column definitions: Ref = group reference code. M/C = Mandatory (without it reconciliation output cannot be produced) or Conditional (required for specific supply types, client profiles, or sub-sections). Mode = Forensic (reactive engagement

after audit notice), Operational (continuous platform user), or Both.

Documents marked Mandatory must be received and validated before any reconciliation output is released. The completeness gate (Anomaly Type T9, Document 1 Section 4) must pass for all Mandatory documents in Forensic Mode. In Operational Mode,

Mandatory documents are ingested at onboarding and refreshed at configured intervals.

Ref Document / Data Extract Source M / C Sections Mode Notes

### CATEGORY A — CLIENT-PROVIDED DOCUMENTS

A1 — Transaction Data (ERP and Books)

**A1.** Sales register — outward Client ERP **Manda** A1,A2,A3,A6,A **Both** Line-item level mandatory. All**01** supply, invoice-level: invoice no, **tory** 7,A8,A9 formats per ADR-056. Primary

date, customer GSTIN, supply source for all output tax sections. type, HSN/SAC, qty, UOM, unit

price, trade discount, add-on charges

(freight/packing/insurance), taxable value, GST rate,

CGST/SGST/IGST, total value, currency, reverse charge flag

**A1.** Credit note and debit note Client ERP **Manda** A1,A2,A8,B8 **Both** Must carry original invoice number.**02** register — with original invoice **tory** Unlinked CNs are an immediate

reference, reason code, finding. adjusted taxable value and tax

**A1.** Purchase register — inward Client ERP **Manda** B1,B2,B3,B4,B **Both** GL code and cost centre mandatory**03** supply, invoice-level: supplier **tory** 5,B8 for Rule 42 pool segregation.

GSTIN, invoice no, date, taxable value, CGST/SGST/IGST, GL

code, cost centre

**A1.** Advance receipt register — Client ERP **Condit** A4 **Both** Required for all service taxpayers.**04** date, amount, customer, supply **ional** Goods advance tracking post

type, GST paid on advance, Notification 66/2017. invoice linkage

**A1.** Unbilled revenue schedule — Client **Condit** A5 **Both** date_of_service_completion is the**05** item-level with Books/ERP **ional** critical field — see Missing Items

date_of_service_completion per Register Item 2. Without it, 30-day item rule is flag-only.

**A1.** Job work dispatch register — Client ERP **Condit** A5 **Both** Dispatch date triggers the**06** goods sent to job worker with **ional** 1-year/3-year statutory return

date_of_goods_dispatch_to_job deadline. See Missing Items worker, qty, description, job Register Item 3.

worker GSTIN

**A1.** Running account / continuous Client **Condit** A5 **Both** For manufacturers on running**07** supply contract register — ERP/Contra **ional** account arrangements. Section

contractual billing frequency, cts 12(4) continuous supply timing statement of account dates, check. See Missing Items Register

delivery records Item 4.

**A1.** Audited Profit and Loss Account Client Books **Manda** A2,A3 **Both** Starting point for GSTR-9C Table 5**08** — full year, all revenue GL **tory** turnover bridge. Every revenue

accounts with narrations credit must be captured and classified.

**A1.** Trial balance — GL-level detail Client Books **Manda** A2,A3,B4 **Both** Maps each GL to GST treatment.**09** with account descriptions and **tory** Required for Rule 42 pool

opening/closing balances segregation and P&L turnover analysis.

**A1.** Balance sheet — full year: Client Books **Manda** A4,A5,B2,B7 **Both** Multiple uses across output and**10** advances from customers, **tory** input tax sections.

unbilled revenue, deferred revenue, creditors, capital

goods schedule

**A1.** Fixed asset register — Client Books **Manda** A3,B7 **Both** Must be asset-level. Rule 44**11** asset-level: purchase date, **tory** disposal reversal cannot be

purchase value, ITC availed, computed from aggregate totals. date of disposal, disposal value,

disposal type

**A1.** Creditor ageing report — Client Books **Manda** B2 **Both** Primary input for 180-day payment**12** outstanding invoices by age as **tory** rule. Invoice date and payment

at period end, per invoice status per invoice required.

**A1.** Payment register — date and Client **Manda** B2 **Both** Bank transfer date is the payment**13** amount of each supplier Books/Bank **tory** date. Cheque issue date is not.

payment linked to invoice Must be cross-verified with bank statement.

**A1.** Bank statements — all outward Client Bank **Manda** B2 **Both** Authoritative record of payment**14** payments to suppliers **tory** dates for 180-day rule compliance.

**A1.** Bank statements — all inward Client Bank **Condit** A8 **Both** Supports FIRC/BRC reconciliation.**15** foreign currency receipts (export **ional** Export proceeds realisation

proceeds) confirmation.

**A1.** Stock / inventory register — Client **Condit** A1,B3 **Both** Required for stock-to-invoice**16** opening and closing stock per Books/WMS **ional** reconciliation and Section 17(5)(h)

SKU with write-off records write-off ITC reversal.

**A1.** Product / SKU master — Client **Condit** A1 **Both** Mandatory for FMCG/retail profiles.**17** MRP_applicable flag (Y/N) and Master Data **ional** Cannot be derived from invoice

declared MRP per SKU register. See Missing Items Register Item 1 (RESOLVED).

**A1.** Inter-GSTIN transaction register Client ERP **Condit** A7 **Both** Required for multi-GSTIN entities.**18** — all stock transfers, **ional** Enables inter-GSTIN supply

cross-charges, capital good declaration and valuation check. transfers between GSTINs

under the same PAN

**A1.** Cost allocation register — HO to Client Books **Condit** A7 **Both** Identifies allocations without invoice**19** branch allocations including **ional** that may constitute taxable

those without invoices inter-GSTIN supplies. Contest item A7.2.

**A1.** Platform settlement reports — Marketplace **Condit** A9 **Both** Required for all marketplace sellers.**20** MTR, GTR, SSR, DRR, VRET Operators **ional** All formats (Excel/CSV) per

for all marketplace platforms ADR-056. Field mapping per operator maintained as

configuration.

**A1.** ITC reversal register — Rule Client **Condit** B3,B4 **Both** Client-maintained reversal records.**21** 42/43 and Section 17(5) ERP/Books **ional** Cross-checked against GSTR-3B

reversals per period declarations.

**A1.** RCM register — RCM category, Client ERP **Condit** B5 **Both** Required for all taxpayers with RCM**22** supplier, invoice, RCM liability **ional** categories. Particularly important for

computed and paid per period import of services.

**A1.** Foreign currency payment Client **Condit** B5 **Both** Every foreign payment assessed for**23** register — all payments to Bank/ERP **ional** import of services RCM.

| overseas vendors: amount, vendor, country, purpose, | Completeness gate — if absent, all import of services checks are |
| --- | --- |
| invoice/contract reference | INCOMPLETE. |

**A1.** Common input service invoices Client ERP **Condit** B6 **Both** Required for ISD distribution**24** at HO — rent, IT, insurance, **ional** verification. Post 01 Oct 2023

audit fees shared across mandatory ISD compliance check. GSTINs

A2 — Corporate Identity and Structure

**A2.** Certificate of Incorporation — Client / MCA **Manda** A6,A7 **Both** Establishes legal identity and date**01** with CIN (Company **tory** from which entity exists. CIN is the

Identification Number), date of anchor for MCA registry incorporation, registered office cross-checks.

address

**A2.** Memorandum and Articles of Client **Condit** A6,A7 **Forens** Establishes authorised business**02** Association — including **ional ic** scope. Relevant for nature of supply

authorised business objects and place of supply determinations.

**A2.** Shareholding pattern — current Client / MCA **Manda** A6,A7 **Both** Identifies entities holding 25% or**03** and for each year in audit period / SEBI **tory** more voting stock — triggers related

person test under Section 15. Must be period-specific.

**A2.** Related party register — all Client **Manda** A6,A7 **Both** Cannot be derived from transaction**04** related persons with Declaration **tory** data. Client must declare at

GSTIN/PAN, nature of onboarding. Without it all A6 checks relationship, ownership are INCOMPLETE.

percentage

**A2.** Ind AS 24 / AS-18 related party Client / **Manda** A6,A7 **Both** Auditor-verified related party list.**05** disclosure from audited financial Auditors **tory** Most reliable starting point. Must be

statements obtained for each year in scope.

**A2.** Joint venture agreement — JV Client **Condit** A6 **Both** Establishes JV partner relationships.**06** partner relationships, ownership **ional** Section 15 related person test —

structure, control provisions both controlling a third person.

**A2.** GSTIN register — all GSTINs Client / **Manda** A7,B6 **Both** Complete GSTIN universe for the**07** under the same PAN with state, GSTN **tory** entity. Missing GSTINs mean

registration type, effective date incomplete inter-GSTIN reconciliation.

A3 — Director and Officer Documents

**A3.** List of directors with Director Client / MCA **Condit** A6,B5 **Both** DIN is the government-issued**01** Identification Number (DIN) — **ional** unique identifier for every director.

current and for each year in Required for RCM determination audit period and related party identification.

**A3.** Form DIR-12 — MCA filing on Client / MCA **Condit** A6,B5 **Forens** Establishes periods of directorship.**02** appointment and cessation of **ional ic** Critical for period-specific RCM

directors with effective dates determination — was this person a director on this date?

**A3.** Board resolution appointing Client **Condit** A6,B5 **Both** Establishes executive director**03** whole-time / managing director **ional** status. Without this, default

- with appointment date and assumption is non-executive (RCM terms applicable at 18%).
**A3.** Director service agreement / Client **Condit** A6,B5 **Both** Establishes employer-employee**04** employment contract for **ional** relationship for Schedule III

whole-time directors exclusion. Must show CTC,

reporting structure, and payroll inclusion.

**A3.** Payroll and TDS register — Client **Condit** A6,B5 **Both** Employee vs contractor**05** Form 16 (TDS 192) per Books/HR **ional** classification. TDS code is the

| employee, TDS 194J per professional, TDS 194C per | primary determinant. Cross-referenced with director list |
| --- | --- |
| contractor | for executive classification. |

**A3.** Key managerial personnel Client / MCA **Condit** A6 **Both** Verifiable against MCA filings.**06** (KMP) list from annual report — **ional** Establishes who holds statutory

with designations positions.

A4 — Contracts and Agreements

**A4.** Cost-sharing agreements — Client **Condit** A6,A7,B5 **Both** Circular 159/15/2021 requires**01** inter-company arrangements for **ional** documented evidence of

shared services, management cost-sharing basis. Nature of fees, IT costs, with basis of cost arrangement determines taxability.

allocation

**A4.** Service contracts with foreign Client **Condit** A8 **Both** Five-condition test under Section**02** customers — nature of service, **ional** 2(6) IGST Act. Contract is primary

recipient jurisdiction, delivery evidence of recipient location and mechanism, payment terms place of supply.

**A4.** Overseas vendor contracts — Client **Condit** B5 **Both** Import of services RCM**03** management fees, technical **ional** identification. Nature of service and

assistance, royalty agreements, payment/invoice dates determine cloud/SaaS subscriptions time of supply.

**A4.** Job work agreement — nature Client **Condit** A5,A1 **Both** Distinguishes job work from contract**04** of arrangement, materials **ional** manufacturing. Affects taxability, ITC

supplied, process performed, entitlement, HSN classification. return timeline

**A4.** Long-duration service contracts Client **Condit** A5 **Both** Required for time of supply**05** — milestone schedule, POCM **ional** determination on milestone-based

basis, billing triggers and POCM-based revenue recognition.

**A4.** Transfer pricing documentation Client / Tax **Condit** A6 **Both** Arm's length price in TP**06** — TP study with arm's length Advisors **ional** documentation is the most

price for related party defensible open market value basis transactions under Section 15(4) Rule 28.

**A4.** Advance Pricing Agreement Client / **Condit** A6 **Both** Higher evidentiary value than TP**07** (APA) — bilateral agreement CBDT **ional** study — government-agreed arm's

with income tax authority on length price for related party arm's length pricing supplies.

**A4.** GTA invoices and consignment Client **Condit** B5 **Both** Consignment note is the trigger**08** notes — per freight payment **ional** document for GTA RCM. Without it,

transport payment is not a GTA supply.

**A4.** Security agency contracts — Client **Condit** B5 **Both** Security agency entity type**09** with supplier entity type **ional** determines forward charge vs RCM.

(company vs Company = forward charge. Others proprietorship/partnership) = RCM.

A5 — Export and Zero-Rated Entitlement Instruments

**A5.** LUT (Letter of Undertaking) — Client / **Condit** A8 **Both** The legal instrument enabling**01** ARN, GSTIN, financial year of GSTN Portal **ional** zero-rated export without IGST

validity. One LUT required per payment. Period-specific — must be financial year per GSTIN valid at date of each export invoice.

**A5.** IEC (Importer Exporter Code) Client / **Condit** A8 **Both** Mandatory code for all export/import**02** certificate from DGFT — IEC DGFT **ional** transactions. Must be linked to

number and linked GSTIN exporting GSTIN. Mismatch blocks ICEGATE-GSTN data flow.

**A5.** SEZ unit's Letter of Approval Client / SEZ **Condit** A8 **Both** LoA number must appear on every**03** (LoA) — issued by Development Authority **ional** supplier invoice to SEZ. LoA validity

Commissioner: unit identity, at supply date is a hard condition for authorised operations, validity zero-rating.

period, permitted goods/services

**A5.** SEZ unit's Letter of Permission Client / **Condit** A8 **Both** Same function as LoA for service**04** (LoP) — for IT/ITES SEZ units Developmen **ional** SEZ units. Validity and authorised

t operations scope must be current at Commission supply date.

er

**A5.** SEZ Operations Client / **Condit** A8 **Both** Zero-rated supply entitlement**05** Commencement Certificate — Developmen **ional** begins from commencement date —

date from which SEZ unit t not from LoA date. Supplies before commenced authorised Commission commencement do not qualify.

operations er

**A5.** SEZ Notification — official Client / **Condit** A8 **Both** Establishes that the recipient's**06** government notification Ministry of **ional** premises are within a notified SEZ.

establishing the SEZ, its Commerce Supply to premises outside notified boundaries, and approved units boundary does not qualify.

**A5.** SEZ Annual Performance Client / **Condit** A8 **Both** Non-approval or suspension of APR**07** Report (APR) — approval status Developmen **ional** can affect SEZ status. Unit with

per year t rejected APR loses zero-rated Commission supply entitlement from suspension

er date.

**A5.** EOU Letter of Permission (LoP) Client / **Condit** A8,A10 **Both** Required for zero-rated supply to**08** from Development Developmen **ional** EOU. NFE (Net Foreign Exchange)

Commissioner — with NFE t obligation status determines obligation and validity Commission continued EOU eligibility.

er

**A5.** STPI registration letter — with Client / STPI **Condit** A8,A10 **Both** Required for suppliers to STPI units**09** registration number, permitted **ional** claiming zero-rated status.

services, and validity

**A5.** EOU Annual Compliance Report Client / **Condit** A8 **Both** Non-compliance can result in LoP**10** — filed with Development Developmen **ional** cancellation and retrospective

Commissioner, approval status t demand on zero-rated supplies Commission received.

er

**A5.** EPCG Licence — licence Client / **Condit** A8 **Both** Required for deemed export claim**11** number, validity, export DGFT **ional** under Notification 48/2017. Licence

obligation quantum, permitted number must appear on invoice. capital goods

**A5.** Advance Authorisation (AA) — Client / **Condit** A8 **Both** Required for deemed export claim.**12** licence number, validity, DGFT **ional** Permitted inputs list must cover the

permitted inputs and export goods supplied. obligation

**A5.** Export Obligation Discharge Client / **Condit** A8 **Both** Closes the export obligation loop.**13** Certificate (EODC) — issued by DGFT **ional** Without EODC, the deemed export

DGFT on fulfilment of export or duty-free entitlement remains obligation contingent.

**A5.** FIRC / BRC register — foreign Client / AD **Condit** A8 **Both** 12-month realisation deadline**14** exchange realisation per export Bank **ional** monitoring. Both FIRC

invoice: date, amount, currency, (bank-issued) and DGFT eBRC AD bank, FIRC number (government-authenticated)

required.

**A5.** SOFTEX form approval — for Client / **Condit** A8 **Both** STPI equivalent of shipping bill for**15** STPI software exporters STPI/RBI **ional** software exports. Required to

certifying software exports establish that supply to STPI unit was a genuine export.

**A5.** Customs Bonded Warehouse Client / **Condit** A8 **Both** GST zero-rating of supplies to EOU**16** licence — for EOU units Customs **ional** is anchored to the bonded premises.

establishing bonded premises Supply to non-bonded premises does not qualify.

**A5.** ITC-04 acknowledgements — Client / **Condit** A5,A1 **Both** Establishes that goods sent to job**17** job work return confirmations GSTN **ional** worker were returned within

filed with GSTN statutory period. Defence against deemed supply on deadline expiry.

A6 — Allied Law Instruments — Customs

**A6.** Bill of Entry — assessed copy Client / **Condit** B1 **Both** Assessed BoE establishes customs**01** and out-of-charge copy per ICEGATE **ional** value and HSN for IGST ITC

import shipment: customs value, computation. Out-of-charge copy HSN, IGST paid, BCD paid confirms clearance.

**A6.** Customs Tariff Classification Client / **Condit** A1,B1 **Both** Entity-specific ruling overrides**02** order / speaking order — Customs **ional** general HSN master for that

| entity-specific classification ruling for specific imported | taxpayer's specific goods. Determines IGST rate and ITC |
| --- | --- |
| goods | quantum. |

**A6.** Customs Duty Exemption Client / **Condit** B1 **Both** Where IGST is also exempt or**03** Certificate and applicable Customs **ional** concessional on import —

notification — for concessional determines ITC available. or nil duty imports Notification applicability and entity

entitlement both required.

**A6.** B-17 Bond — customs bond for Client / **Condit** A8,B1 **Both** Establishes EOU's bonded status.**04** EOUs permitting duty-free Customs **ional** Breach of bond conditions can

import and manufacture retrospectively affect zero-rating of related supplies.

**A6.** BLUT (Bond with Letter of Client / **Condit** A8 **Both** Export entitlement instrument for**05** Undertaking) — for exports Customs **ional** customs. Relevant where GST and

without payment of customs customs interact on export duty transactions.

**A6.** AEO (Authorised Economic Client / **Condit** A8 **Both** AEO status affects customs**06** Operator) certificate — issued Customs **ional** facilitation and supply chain cost

by Customs with validity period structures. Certificate validity must be current.

A7 — Allied Law Instruments — FEMA / RBI

**A7.** RBI approval for delayed export Client / RBI **Condit** A8 **Both** Where FIRC/BRC not received**01** realisation — extension beyond **ional** within 12 months and RBI extension

12-month standard period obtained — this approval sustains the zero-rating. Without it, IGST

becomes payable with interest from export date.

**A7.** RBI Compounding Order — for Client / RBI **Condit** A8 **Forens** Compounding order establishes the**02** compounded FEMA violations **ional ic** settled position on the FEMA

related to export proceeds or violation. Relevant where export foreign currency zero-rating is challenged due to

irregular realisation.

**A7.** ODI (Overseas Direct Client / RBI **Condit** A6,B5 **Both** Establishes related party**03** Investment) approval — for **ional** relationship with overseas

companies with overseas subsidiary. Relevant for import of subsidiaries services RCM identification.

**A7.** Form 15CA / 15CB — Client / CA **Condit** B5 **Both** Form 15CB (CA certificate)**04** remittance certificates filed for **ional** classifies the nature of overseas

overseas payments payment. Classification determines RCM applicability and treaty

position.

**A7.** SCOMET licence — for export Client / **Condit** A8 **Both** Supply of SCOMET items without**05** of special chemicals, organisms, DGFT **ional** valid licence has GST and customs

materials, equipment and implications. Required where technologies taxpayer exports SCOMET items.

### CATEGORY B — GST PORTAL AND GOVERNMENT DATA (Platform retrieves via API or portal download)

B1 — GSTN Filed Returns and Statements

**B1.** GSTR-1 — all periods, all GSTN API **Manda** A1,A2,A7,A8,A **Both** The government record of declared**01** tables: B2B (T4), B2CL (T5), **tory** 9,A10 outward supply. Primary

EXP (T6A/6B), B2CS (T7), reconciliation counterpart to client EXEMP (T8), CDNR (T9), sales register.

CDNUR (T10), AT (T11), amendments (T9A/9B/9C)

**B1.** GSTR-1 ARN and filing date — GSTN API **Manda** A1,A6 **Both** Confirms return was filed. Delayed**02** per period, per GSTIN **tory** filings trigger interest computation

from due date.

**B1.** GSTR-2B — all periods: GSTN API **Manda** B1,B2,B5,B6,B **Both** Authoritative ITC availability**03** auto-populated ITC statement **tory** 8 statement. Cap on claimable ITC

with invoice-level ITC from post Section 16(2)(aa) from 01 Jan supplier GSTR-1, ISD credits, 2022.

TCS, CN/DN entries

**B1.** GSTR-2B amendment history — GSTN API **Manda** B1,B8 **Both** Supplier amendments change**04** supplier amendments to **tory** buyer's GSTR-2B. Affects ITC

previously filed invoices already claimed in prior periods. affecting buyer GSTR-2B

**B1.** GSTR-3B — all periods: Table GSTN API **Manda** A1,A10,B1,B2, **Both** Filed tax payment return. What was**05** 3.1 (outward supply liability), **tory** B3,B4,B5,B6,B declared and paid. Compared

Table 4 (ITC availed and 7 against computed obligations across reversed), Table 5 all sections.

(exempt/nil/non-GST), Table 6 (tax payments)

**B1.** GSTR-9 Annual Return — GSTN API **Manda** A1,A2,A10,B1, **Both** Annual consolidated declaration.**06** Tables 4, 5, 6, 7, 8, 9, 11, 14 **tory** B4 Formal reconciliation target for

turnover and ITC.

**B1.** GSTR-9C Reconciliation GSTN API **Manda** A2,A3 **Both** Platform computes the bridge**07** Statement — Table 5 (turnover **tory** independently and compares

bridge, auditor certified) and against the filed GSTR-9C. Table 12 (ITC reconciliation)

**B1.** GSTR-2A — historical GSTN API **Condit** B1 **Forens** Required for Forensic engagements**08** (pre-GSTR-2B periods, **ional ic** covering periods before GSTR-2B

pre-January 2020) was introduced.

**B1.** GSTR-6 / GSTR-6A — ISD GSTN API **Condit** B6 **Both** Required for clients operating as**09** return: distribution amounts per **ional** ISD. Distribution amounts and tax

recipient GSTIN per period heads per recipient GSTIN.

**B1.** GSTR-7 — TDS deducted by GSTN API **Condit** A1 **Both** TDS deducted reduces net tax**10** specified persons (government **ional** payable. Relevant for clients

entities) receiving payments from government entities.

**B1.** GSTR-8 — TCS by e-commerce GSTN API **Condit** A9 **Both** Operator's declared TCS base per**11** operators: seller GSTIN-wise, **ional** seller GSTIN. Primary check for

state-wise TCS for all periods marketplace seller TCS reconciliation.

B2 — GST Registration and Status Documents

**B2.** GST Registration Certificate GSTN Portal **Manda** A1,A6,A7,B6 **Both** Foundational identity document.**01** (REG-06) — for every GSTIN of **tory** Registration type determines ITC

the taxpayer: legal name, trade eligibility, return obligations, and name, registration type, composition status.

principal place of business, additional places, effective date,

authorised signatories

**B2.** GST Registration Certificate — GSTN API **Manda** A1,A6,B1 **Both** B2B ITC entitlement verification.**02** key customers: registration type, **tory** Supply to composition dealer cannot

active/cancelled status at carry ITC to buyer. invoice date

**B2.** GST Registration Certificate — GSTN API **Manda** B1,B3 **Both** ITC on purchases from unregistered**03** key suppliers: registration type, **tory** suppliers (outside RCM) is not

active/cancelled status at available. Cancellation at invoice invoice date date voids ITC.

**B2.** GST Registration amendment GSTN Portal **Condit** A1,A7 **Both** Material for period-specific**04** history — changes to authorised **ional** entitlement determination.

signatories, additional places, Registration type changes affect ITC registration type eligibility.

**B2.** Composition scheme opt-in GSTN Portal **Condit** A1,B1,B3 **Both** During composition periods:**05** (CMP-01) and opt-out (CMP-04) **ional** taxpayer cannot issue tax invoices.

records with effective dates Buyers cannot claim ITC on purchases from composition

suppliers.

**B2.** ISD Registration Certificate — GSTN Portal **Condit** B6 **Both** Establishes legal basis for ITC**06** with effective date of ISD **ional** distribution. Post 01 Oct 2023:

registration mandatory if common input services exist at HO. Without ISD

registration, branch ITC claims via cross-charge are invalid.

**B2.** GSTIN GSTN API **Manda** A1,B1 **Both** GSTIN validity at invoice date.**07** active/cancelled/suspended **tory** Retrospective cancellation

| status at specific dates — for all supplier and customer GSTINs | identification. Active status at date of supply is the test — not current |
| --- | --- |
| transacted with | status. |

B3 — Notices, Proceedings, and Settlement Instruments

**B3.** Open Show Cause Notice GSTN Portal **Manda** All **Both** Platform must not re-flag an issue**01** (SCN) register — all SCNs / Client **tory** already subject to an SCN as an

| issued: period covered, issue raised, amount demanded, | unaddressed anomaly. Stage of proceedings determines response |
| --- | --- |
| current stage | pathway. |

_B3._ DRC-01B notices — GSTR-1 vs GSTN Portal _Condit_ A1 _Both_ System-generated notices for_02_ GSTR-3B mismatch notices _ional_ GSTR-1/GSTR-3B discrepancies.

issued by GSTN system Must be reconciled and responded to.

**B3.** DRC-03 voluntary payment GSTN Portal **Condit** All **Both** Prior voluntary payments reduce**03** records — all voluntary **ional** quantum of any demand. Critical in

payments made with period, Forensic Mode to avoid issue, and amount double-counting of payments

already made.

**B3.** Amnesty scheme participation GSTN Portal **Condit** All **Forens** Settlement order establishes that**04** certificate / settlement order — / Client **ional ic** liability for that period and issue is

Vivad Se Vishwas or similar closed. Platform must not re-raise schemes settled issues.

**B3.** Court order / stay order specific Client / **Condit** All **Both** A demand subject to a court stay is**05** to the taxpayer — HC, SC, Court **ional** not an open liability. Platform must

CESTAT orders staying or Registry record stay orders and exclude deciding specific GST demands stayed demands from open anomaly

count.

**B3.** Personal hearing orders and GSTN Portal **Condit** All **Forens** Establishes the current stage of**06** adjudication orders — issued by / Client **ional ic** proceedings. Required in Forensic

GST officer during Mode to understand which issues audit/assessment proceedings are already before the officer.

**B3.** Refund applications (RFD-01) GSTN Portal **Condit** A8 **Both** Export refund tracking. 60-day**07** and status — including **ional** processing deadline monitoring.

deficiency memos and GSTN Deficiency memo identification and processing status response.

B4 — E-Invoice and E-Way Bill Portal Data

**B4.** IRN / IRP register — all IRNs IRP API **Condit** A1 **Both** Required for taxpayers above**01** generated: invoice number, **ional** e-invoice threshold. IRP timestamp

date, GSTIN, taxable value, IRP is authoritative invoice date. Invoice acknowledgement number and without IRN is legally defective.

timestamp

**B4.** E-Way Bill outward register — EWB Portal **Manda** A1 **Both** Outward goods movement**02** all EWBs generated by the API **tory** validation. Three-way match with

taxpayer: EWB number, invoice invoice and GSTR-1.

number, value, HSN, from-to state, transporter

**B4.** E-Way Bill threshold history — INAFIN **Manda** A1 **Both** Determines which transactions**03** effective dates of each threshold Corpus / **tory** required EWB at the date of supply.

change from April 2018 to EWB Portal Bitemporal — threshold has current changed multiple times.

**B4.** E-invoice threshold history — INAFIN **Manda** A1 **Both** Determines which taxpayers were**04** effective dates of each threshold Corpus / IRP **tory** mandatory e-invoice filers at any

change from October 2020 to Portal given date. Invoice without IRN is current only a finding if taxpayer was above

threshold.

B5 — ICEGATE and Customs Portal Data

**B5.** ICEGATE shipping bill data — ICEGATE **Condit** A8 **Both** Three-way match with sales invoice**01** all shipping bills: shipping bill API **ional** and GSTR-1 Table 6A. EGM status

number, date, invoice reference, check. Required for all goods FOB value, HSN, port code, exporters.

exporter GSTIN, IEC

**B5.** ICEGATE EGM (Export General ICEGATE **Condit** A8 **Both** Confirms goods have left India.**02** Manifest) status — per shipping API **ional** Refund cannot be processed until

bill: filed/not filed EGM is filed by the shipping line.

**B5.** Bill of Entry data from ICEGATE ICEGATE **Condit** B1 **Both** Import IGST credit reconciliation.**03** — IGST paid on imports per API / Client **ional** Separate from GSTR-2B — flows

BoE from ICEGATE not from supplier GSTR-1.

B6 — DGFT Portal Data

**B6.** DGFT eBRC (Electronic Bank DGFT Portal **Condit** A8 **Both** Government-authenticated version**01** Realisation Certificate) — **ional** of export realisation. Distinct from

government-authenticated bank-issued FIRC/BRC. Both export proceeds realisation per required for zero-rating defence.

invoice

**B6.** IEC registry — IEC number, DGFT Portal **Condit** A8 **Both** IEC-GSTIN linkage validation.**02** linked GSTIN, validity status **ional** Mismatch between IEC on shipping

bill and GSTIN in GSTR-1 blocks refund processing.

**B6.** Export obligation status — DGFT Portal **Condit** A8 **Both** Defaulted export obligation**03** outstanding vs fulfilled per **ional** retroactively affects deemed export

EPCG/AA licence zero-rating claims. Must be current status.

### CATEGORY C — PLATFORM REGULATORY CORPUS (INAFIN-resident — not client-provided)

C1 — Primary Legislation (Acts)

**C1.** CGST Act 2017 — all sections INAFIN **Manda** All sections **Both** Foundational legal basis. Sections 7**01** with amendment history Corpus **tory** (supply), 9 (levy), 15 (valuation), 16

(Finance Acts 2018 through (ITC), 17 (blocked credits), 20 (ISD), 2025) 25 (registration), 34 (credit notes),

52 (TCS). Bitemporal.

**C1.** IGST Act 2017 — all sections INAFIN **Manda** A1,A7,A8 **Both** Place of supply and zero-rating**02** with amendment history, Corpus **tory** framework. Section 13

including Sections 2(6) (export service-specific rules are critical for of services), 10–13 (place of export of services classification.

supply), 16 (zero-rated supply)

**C1.** UTGST Act 2017, INAFIN **Manda** A1 **Both** UTGST for Union Territory supplies.**03** Compensation Cess Act 2017 Corpus **tory** Cess for specified goods (tobacco,

- with amendment history luxury vehicles, aerated drinks).
**C1.** Customs Act 1962 and Customs INAFIN **Condit** A8,B1,A6 **Both** Customs law provisions that directly**04** Tariff Act 1975 — relevant Corpus **ional** affect GST treatment — particularly

provisions for import IGST, SEZ, for imports, SEZ/EOU supplies, and EOU deemed exports.

**C1.** IGST (Extension to Jammu and INAFIN **Condit** A1,A7 **Both** J&K-specific provisions applicable to**05** Kashmir) Act, CGST (Extension Corpus **ional** supplies to/from J&K-registered

to J&K) Act — if relevant to entities. client operations

C2 — Subordinate Legislation (Rules and Notifications)

**C2.** CGST Rules 2017 — all rules INAFIN **Manda** A1,A2,A6,B1,B **Both** Rules are the operational**01** with amendment history: Rules Corpus **tory** 2,B4,B5,B6,B7 mechanism. Bitemporal essential —

27–35 (valuation), Rule 36 (ITC rules amended frequently. Rate and documents), Rule 37 (180-day), procedure rules are both covered.

Rule 39 (ISD), Rule 42 (inputs reversal), Rule 43 (capital goods

reversal), Rule 44 (disposal), Rule 46 (invoice), Rule 48

(e-invoice), Rule 86B, Rule 89 (refund)

**C2.** GST Rate Notifications — INAFIN **Manda** A1,A3,A10,B3 **Both** Rate applicable at date of supply for**02** CGST (Rate) and IGST (Rate) Corpus / **tory** each HSN/SAC. Most

from July 2017 to current: CBIC query-intensive corpus dataset. HSN/SAC rate schedule with Every rate change bifurcates the

effective dates per rate revision schedule.

**C2.** Exemption Notifications — INAFIN **Manda** A3,A10 **Both** Exempt supply classification.**03** Notification 12/2017 CGST Corpus **tory** Conditions matter as much as the

(Rate) and all amendments: category. Bitemporal — exemptions exempt service categories with added, modified, withdrawn.

conditions and effective dates

**C2.** RCM Notification 13/2017 CGST INAFIN **Manda** B5 **Both** RCM category list. Bitemporal —**04** and all amendments — Section Corpus **tory** categories added and removed.

9(3) notified categories with Must apply notification in force at supplier/recipient classification relevant invoice date.

and effective dates

**C2.** Targeted and time-bound INAFIN **Condit** A3,A10 **Both** CRITICAL: These are distinct from**05** exemption notifications — Corpus **ional** standing exemptions in Notification

| event-specific, time-limited, or sector-specific exemptions (e.g., | 12/2017. Must be maintained separately with precise effective |
| --- | --- |
| COVID vaccine supply exemption, disaster relief | periods and HSN scope. Entity must also hold a specific entitlement |
| notifications) | instrument (Group D5) establishing their coverage under the targeted |

notification.

**C2.** Notification 48/2017 — Deemed INAFIN **Condit** A8 **Both** All conditions must be met**06** exports (EOU/EPCG/AA supply) Corpus **ional** simultaneously. Conditions have

with conditions and permitted been amended — bitemporal check categories essential.

**C2.** Notification 66/2017 — Deferral INAFIN **Condit** A4 **Both** Confirms goods advance GST is**07** of GST on advances for goods Corpus **ional** deferred to time of supply. Was this

notification in force at the relevant date?

**C2.** Place of supply notifications and INAFIN **Manda** A1,A7,A8 **Both** Determines inter vs intra-state.**08** rules — Sections 10–13 IGST Corpus **tory** IGST vs CGST+SGST. Section 13

Act with specific service service-specific overrides category overrides (immovable property, events,

transport, telecom, banking).

**C2.** Time of supply provisions — INAFIN **Manda** A1,A4,A5,B5 **Both** When GST liability arises. Governs**09** Sections 12–13 CGST Act with Corpus **tory** interest computation start date.

all amendments 30-day services rule and 60-day RCM import rule.

**C2.** CBEC notified exchange rates INAFIN **Condit** A1,A8 **Both** Required for foreign currency**10** — by date and currency pair for Corpus / **ional** invoice INR conversion.

all relevant currencies CBIC Date-specific CBEC rate — not bank rate, not RBI rate.

**C2.** Legal Metrology (Packaged INAFIN **Condit** A1 **Both** MRP applicability determination for**11** Commodities) Rules 2011 and Corpus **ional** FMCG and retail profile clients.

amendments — scheduled Exclusion list determines which

commodities and MRP commodities are outside MRP declaration requirements scope.

**C2.** Finance Act 2023 — ISD INAFIN **Condit** B6 **Both** Mandatory ISD compliance check**12** mandatory amendment Corpus **ional** for periods post Oct 2023.

provisions (effective 01 Oct Cross-charge is no longer a valid 2023) alternative for common input

services.

**C2.** E-invoicing threshold INAFIN **Manda** A1 **Both** Determines e-invoice mandatory**13** notifications — all six threshold Corpus **tory** status at any given date. Invoice

events from October 2020 to without IRN is only a finding if current taxpayer was above threshold at

invoice date.

C3 — CBIC Circulars and Instructions

**C3.** CBIC Circular 88/07/2019 — INAFIN **Manda** A1 **Both** Authoritative position on tax head**01** Tax head correction (IGST vs Corpus **tory** error remediation. Contest item

CGST+SGST): process for A1.9. correcting wrong tax head

payment

**C3.** CBIC Circular 92/11/2019 — INAFIN **Manda** A1 **Both** Standing and post-sale discount**02** Trade discounts: conditions for Corpus **tory** conditions. Contest item A1.2.

deductibility from taxable value

**C3.** CBIC Circular 140/10/2020 — INAFIN **Manda** A6,B5 **Both** Operative position on director RCM.**03** Director remuneration: executive Corpus **tory** Whole-time directors are

vs non-executive RCM employees; non-executive directors classification attract RCM.

**C3.** CBIC Circular 159/15/2021 — INAFIN **Manda** A6,A7,B5 **Both** Establishes basis for cost-sharing**04** Cost-sharing between related Corpus **tory** arrangement taxability

entities: taxability framework determination. Requires documented cost-sharing

agreement.

**C3.** CBIC Circular 167/2021 — Food INAFIN **Condit** A9 **Both** Restaurant app sales nil-rated post**05** delivery platform operators: GST Corpus **ional** 01 Jan 2022. Operator pays GST.

liability shift to operator from Contest item A9 food delivery. restaurant (effective 01 Jan

2022)

**C3.** CBIC Circular 170/02/2022 — INAFIN **Condit** A1 **Both** Clarifies IRN mandate applicability**06** E-invoice: validity of invoice Corpus **ional** by period and threshold. Contest

without IRN where threshold item A1.7. applicability is contested

**C3.** CBIC Circular 172/04/2022 — INAFIN **Manda** A3,A6,B3 **Both** Factories Act canteen obligation —**07** Canteen and transport: ITC Corpus **tory** ITC available, recovery not taxable.

availability and employee Contest items A3.4, B3.3. recovery taxability under

statutory obligation

**C3.** CBIC Circular 178/10/2022 — INAFIN **Manda** A2,A3 **Both** Operative post-circular position on**08** Liquidated damages: not Corpus **tory** LD. Pre-circular periods are

taxable as tolerating an act for contested. Contest item A2.2. agreed deductions

**C3.** CBIC Circular 183/15/2022 — INAFIN **Manda** B1 **Both** Critical for ITC recovery opportunity**09** ITC time limit: claimable in any Corpus **tory** identification. Contest item B1.2.

return before September of following year, not restricted to

period of first GSTR-2B appearance

**C3.** CBIC Circular 187/19/2023 — INAFIN **Condit** A7 **Both** Expanded scope of taxable**10** Corporate guarantee: taxability Corpus **ional** inter-company supplies. Contest

of guarantee issued by holding item A7.1. company for subsidiary

**C3.** All other CBIC Circulars — 2017 INAFIN **Manda** All sections **Both** Complete circular series required.**11** to current, full series Corpus **tory** Clarificatory positions change the

operative tax treatment on specific

issues. Bitemporal indexing mandatory.

C4 — Advance Rulings and Case Law

**C4.** AAR / AAAR rulings — all INAFIN **Condit** All sections **Both** Jurisdiction-specific positions on**01** states, all years: with fact Corpus **ional** contested supply types. Binding on

pattern, provisions invoked, (InstaVAT / applicant, persuasive for others. HSN codes, outcome Indian Required for contest item citation.

Kanoon)

**C4.** HC / SC / CESTAT judgments INAFIN **Condit** All sections **Both** Judicial precedents for CA defence**02** — GST-relevant: with Corpus **ional** narrative. Appeal status determines

provisions, HSN, outcome, (Indian whether the position is settled or still appeal status Kanoon) contested.

**C4.** Advance Ruling obtained by the Client / AAR **Condit** All sections **Both** CRITICAL DISTINCTION: An AAR**03** specific taxpayer (AAR/AAAR) Authority **ional** obtained by the taxpayer is binding

- entity-specific ruling on their on them and on the jurisdictional specific supply officer. The platform's corpus
position must be overridden by the taxpayer's own AAR where it exists.

Stored in Group D5.

C5 — Audit Manuals and Reference Data

**C5.** CBIC GST Audit Manual 2019 INAFIN **Manda** All sections **Both** Functional specification for audit**01** — full text with chapter-level Corpus **tory** checks. The department auditor's

audit checklists playbook. INAFIN platform audit checks are benchmarked against

this manual.

**C5.** Model All India GST Audit INAFIN **Manda** All sections **Both** Updated audit framework.**02** Manual 2023 — full text Corpus **tory** Supersedes 2019 on overlapping

provisions. Both manuals maintained — 2023 does not

entirely replace 2019.

**C5.** HSN / SAC master with full rate INAFIN **Manda** A1,A3,A10,B3 **Both** Most query-intensive corpus**03** history — July 2017 to current, Corpus / **tory** dataset. Every rate change creates

per notification, per effective CBIC a bifurcation. Approximately date 180,000 rows in rate history.

Source: InstaVAT export preferred.

**C5.** RCM liability table — notified INAFIN **Manda** B5 **Both** Structured reference data for RCM**04** categories with effective dates Corpus / **tory** engine. Queryable by supply

and supplier/recipient CBIC category and effective date. classification

**C5.** GSTN return form schema INAFIN **Manda** All sections **Both** Required to parse historical filings**05** versions — JSON schema per Corpus / **tory** correctly. Form schema has

form type per version: GSTR-1, GSTN changed over time — historical GSTR-2B, GSTR-3B, GSTR-9, returns must be parsed against the

GSTR-9C schema version in force when they were filed.

### CATEGORY D — EXTERNAL THIRD PARTY DATA

D1 — Marketplace and E-Commerce Operator Data

**D1.** Amazon MTR (Monthly Tax Amazon **Condit** A9 **Both** Primary source for Amazon seller**01** Report) — order-level: order Seller **ional** GSTR-1 preparation. Must be

date, invoice date, GSTIN, Central obtained for all months in scope. state, taxable value, tax rate,

IGST/CGST/SGST, invoice type

**D1.** Flipkart GTR (GST Tax Report) Flipkart **Condit** A9 **Both** Flipkart equivalent of Amazon MTR.**02** — transaction-level GST data: Seller Hub **ional** Field mapping differs — platform

seller invoice number, buyer maintains operator-specific field state, tax amounts maps as configuration.

**D1.** SSR (Seller Settlement Report) Amazon / **Condit** A9 **Both** TCS reconciliation and net revenue**03** — financial settlement: TCS Flipkart **ional** verification. Settlement cycle does

deducted, marketplace fee, not align with GST monthly period.

shipping charges, net payout per settlement cycle

**D1.** DRR (Disbursement / Amazon / **Condit** A9 **Both** Final link in settlement chain. Bank**04** Remittance Report) — bank Flipkart **ional** receipts + TCS + fees must equal

transfer confirmation: net gross platform sales. amount paid to seller bank

account

**D1.** VRET / CCOGS (Return Report) Amazon / **Condit** A9 **Both** Drives credit note issuance and**05** — customer returns: order-level Flipkart **ional** GSTR-1 CDNR entries. Every return

returns with return reason, date, must have a corresponding credit refund amount, tax impact note.

**D1.** Swiggy / Zomato restaurant Swiggy / **Condit** A9 **Both** Post 01 Jan 2022: restaurant app**06** partner order report — Zomato **ional** sales are nil-rated (operator pays

order-level sales data with GST GST). Circular 167/2021 governs. classification post 01 Jan 2022

**D1.** Other marketplace settlement Respective **Condit** A9 **Both** Format varies by platform. All**07** reports — Meesho, Myntra, platforms **ional** ingested via ADR-056 accepted

Nykaa, Snapdeal and others as formats. Field mapping maintained applicable as platform configuration.

D2 — Banking and Foreign Exchange Documents

**D2.** FIRC (Foreign Inward AD Bank **Condit** A8 **Both** Bank-issued confirmation of foreign**01** Remittance Certificate) — per **ional** exchange receipt. Required for LUT

export invoice: date, amount, export zero-rating. Distinct from currency, remitter, AD bank DGFT eBRC (B6.01).

reference

**D2.** BRC (Bank Realisation AD Bank **Condit** A8 **Forens** Required for Forensic engagements**02** Certificate) — older format **ional ic** covering periods before eBRC

pre-eBRC for export realisation system was implemented. confirmation

D3 — SEZ and EOU Authority Documents

**D3.** SEZ Bill of Entry — goods SEZ Unit **Condit** A8 **Both** Required for zero-rating of goods**01** receipt confirmation at SEZ (Buyer) **ional** supply to SEZ units. Absence

gate: invoice reference, goods means goods did not enter the SEZ description, value, date of entry — zero-rating is disqualified.

**D3.** SEZ inter-unit supply Developmen **Condit** A8,A7 **Both** Specific permissions required for**02** permissions — Development t **ional** inter-unit supplies within SEZ.

Commissioner approval for Commission Without permission, the supply may supplies between units within er not qualify for zero-rated treatment.

the same SEZ

D4 — MCA and Statutory Registry Data

**D4.** MCA21 registry data — annual MCA Portal **Condit** A6,A3 **Both** Verifiable source for director history,**01** filings, Form DIR-12 (director **ional** shareholding pattern, and corporate

changes), Form MGT-7 (annual structure. Cross-reference for return) with shareholding pattern related party identification.

**D4.** NCLT admission order — for NCLT / **Condit** B2 **Both** Establishes moratorium date.**02** suppliers under IBC Client **ional** Required for 180-day payment rule

proceedings: moratorium date exception where supplier is in under Section 14 IBC insolvency. Contest item B2.1.

**D4.** IBC resolution plan (if approved) NCLT / **Condit** B2 **Forens** Establishes payment terms for IBC**03** — payment terms agreed under Client **ional ic** supplier. Determines ITC recovery

insolvency resolution process position post-resolution.

D5 — Targeted and Event-Driven Entitlement Instruments

**D5.** Advance Ruling obtained by the AAR **Condit** All sections **Both** Binding on applicant and**01** specific taxpayer (AAR/AAAR) Authority / **ional** jurisdictional officer. OVERRIDES

- on their specific supply type, Client general corpus position for that HSN, or tax position specific taxpayer on that specific
issue. Must be stored and queried

before anomaly is raised on the covered issue.

**D5.** Entity-specific targeted Relevant **Condit** A3,A10 **Both** For exemptions that are not blanket**02** exemption instrument — Government **ional** (e.g., COVID vaccine supply

government order, scheme Authority / exemption — specific to empanelled notification, or procurement Client manufacturers). Two-part check: (1)

| entitlement establishing the entity's coverage under a | targeted notification in corpus (C2.05); (2) entity's entitlement |
| --- | --- |
| time-bound or targeted exemption | instrument establishing their coverage under that notification. |

BOTH required.

**D5.** Entity-specific CBIC / CBIC / **Condit** All sections **Both** Not published — entity-specific. If**03** jurisdictional officer clarification Jurisdictional **ional** taxpayer acted in good faith on a

| — written position issued to the specific taxpayer on a specific | GST Commission | specific written clarification, it is a defence instrument even if the |
| --- | --- | --- |
| issue | er / Client | general law position has subsequently changed. |

**D5.** Investment incentive scheme State **Condit** A3,A10 **Both** State-specific. Scheme notification +**04** approval — state government Government **ional** entity's individual approval letter +

order granting GST-linked / Client compliance conditions are all benefit (SGST refund, required. Very relevant for large

exemption on specific supplies, manufacturing clients in deferral) incentive-granting states.

**D5.** GST Practitioner authorisation GSTN Portal **Condit** All sections **Both** Required where a GST Practitioner**05** (GST PCT-05) — scope of / Client **ional** is accessing the GSTN portal on

practitioner authority per GSTIN behalf of the taxpayer. Establishes scope of authorised access.

Relevant for CA firm engagement model.

**D5.** Court / stay order specific to the Court **Condit** All sections **Both** A demand subject to a court stay is**06** taxpayer — HC, SC, or CESTAT Registry / **ional** a managed position, not an open

order staying a specific GST Client anomaly. Platform must record and demand or deciding a specific exclude stayed demands from open

issue in the taxpayer's favour anomaly count.

**D5.** GST amnesty / settlement GSTN / **Condit** All sections **Forens** Settlement order closes the liability**07** scheme participation certificate Client **ional ic** for that period and issue. Platform

- Vivad Se Vishwas or similar: must not re-raise issues settled settled period, settled issue, under amnesty schemes.
settlement amount

## 2.1 Source Document Count by Category and Group

| Ref | Group | Total | Mandatory | Conditional |
| --- | --- | --- | --- | --- |
| A1 | Transaction Data (ERP and Books) | 24 | 10 | 14 |
| A2 | Corporate Identity and Structure | 7 | 4 | 3 |
| A3 | Director and Officer Documents | 6 | 0 | 6 |
| A4 | Contracts and Agreements | 9 | 0 | 9 |
| A5 | Export and Zero-Rated Entitlement Instruments | 17 | 0 | 17 |
| A6 | Allied Law Instruments — Customs | 6 | 0 | 6 |
| A7 | Allied Law Instruments — FEMA / RBI | 5 | 0 | 5 |
| B1 | GSTN Filed Returns and Statements | 11 | 7 | 4 |
| B2 | GST Registration and Status Documents | 7 | 4 | 3 |
| B3 | Notices, Proceedings, and Settlement Instruments | 7 | 1 | 6 |
| B4 | E-Invoice and E-Way Bill Portal Data | 4 | 3 | 1 |

| B5 | ICEGATE and Customs Portal Data | 3 | 0 | 3 |
| --- | --- | --- | --- | --- |
| B6 | DGFT Portal Data | 3 | 0 | 3 |
| C1 | Primary Legislation (Acts) | 5 | 3 | 2 |
| C2 | Subordinate Legislation (Rules and Notifications) | 13 | 8 | 5 |
| C3 | CBIC Circulars and Instructions | 11 | 8 | 3 |
| C4 | Advance Rulings and Case Law | 3 | 0 | 3 |
| C5 | Audit Manuals and Reference Data | 5 | 5 | 0 |
| D1 | Marketplace and E-Commerce Operator Data | 7 | 0 | 7 |
| D2 | Banking and Foreign Exchange Documents | 2 | 0 | 2 |
| D3 | SEZ and EOU Authority Documents | 2 | 0 | 2 |
| D4 | MCA and Statutory Registry Data | 3 | 0 | 3 |
| D5 | Targeted and Event-Driven Entitlement Instruments | 7 | 0 | 7 |
| TOTAL | All Groups | 166 | 53 | 113 |

53 Mandatory documents must be present before reconciliation output is produced. 113 Conditional documents are required for specific supply types, client profiles, engagement scope, or sub-sections. The total of 166 represents the complete source

document universe for the INAFIN GST Audit Reconciliation Framework covering sections A1 through A10 and B1 through B8 including all output tax, input tax credit, entity entitlement, and allied law dimensions.

## 2.2 Minimum Onboarding Data Request — All Client Profiles

The following documents must be received at the start of every engagement before the completeness gate clears and reconciliation output is released. Conditional documents are requested based on client profile established at onboarding

screening.

| # Document | Ref | Profile Scope |
| --- | --- | --- |
| 1 Sales register — full period in scope, line-item level | A1.01 | All profiles |
| 2 Purchase register — full period, line-item level with GL code | A1.03 | All profiles |
| 3 Credit note and debit note register with original invoice reference | A1.02 | All profiles |
| 4 Audited P&L and Trial Balance — full year | A1.08/09 | All profiles |
| 5 Balance sheet — full year | A1.10 | All profiles |

_6_ Fixed asset register — asset-level with ITC details and disposal records A1.11 _All profiles_

| 7 Creditor ageing report and payment register with bank-verified payment dates | A1.12/13 | All profiles |
| --- | --- | --- |
| 8 Bank statements — outward payments to suppliers and inward foreign receipts | A1.14/15 | All profiles |
| 9 Related party register with GSTIN/PAN and nature of relationship (client declaration) | A2.04 | All profiles |
| 10 Ind AS 24 / AS-18 related party disclosures from audited accounts | A2.05 | All profiles |
| 11 Shareholding pattern — per year in audit scope | A2.03 | All profiles |
| 12 GSTIN register — all GSTINs under the same PAN with state and registration type | A2.07 | All profiles |
| 13 GST Registration Certificates (REG-06) for all GSTINs of the taxpayer | B2.01 | All profiles |

| 14 Open SCN register — all open show cause notices with period, issue, and current stage | B3.01 | All profiles |
| --- | --- | --- |
| 15 DRC-03 voluntary payment records — all prior voluntary payments | B3.03 | All profiles |
| 16 List of directors with DIN and board resolutions on executive appointments | A3.01/03 | All profiles |
| 17 Any entity-specific AAR/AAAR rulings obtained by the taxpayer | C4.03/D5.01 | All profiles |
| 18 Any court stay orders or amnesty settlement orders relevant to periods in scope | D5.06/07 | All profiles |
| 19 LUT ARN and validity confirmation — for exporters | A5.01 | Exporters |
| 20 FIRC/BRC register — for exporters | A5.14 | Exporters |
| 21 SEZ LoA/LoP, EOU LoP, STPI registration — for zero-rated supply taxpayers | A5.03/08/09 | SEZ/EOU/STPI suppliers |
| 22 Platform settlement reports (MTR/GTR/SSR/DRR/VRET) — for marketplace sellers | A1.20/D1.01-07 | eCommerce |
| 23 Foreign currency payment register — for import of services RCM assessment | A1.23 | Corporates with overseas vendors |
| 24 Product / SKU master with MRP flag — for FMCG and retail profiles | A1.17 | FMCG / Retail |
| 25 Investment incentive scheme approvals — if applicable | D5.04 | Incentive scheme beneficiaries |

This checklist is the minimum data request. The CA engagement team issues this to the client at the start of every engagement. Conditional items (items 19–25) are requested based on the client profile screening at onboarding. Additional documents from the

full register are requested as the reconciliation engine identifies specific supply types, entitlement claims, or proceeding stages that require them.
