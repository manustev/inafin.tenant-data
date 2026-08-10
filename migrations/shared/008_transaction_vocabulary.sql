-- =============================================================================
-- Shared migration 008 — vocabulary for Archetype 1 (transaction line records).
--
-- ARCHITECTURE.md 6: eleven in-scope document types are the same object. A
-- sales register, a purchase register, a credit note register, a Bill of Entry,
-- a shipping bill, an e-invoice register and an e-way bill register all have
-- one shape — a document header with a counterparty, a date and totals, over
-- line items with an HSN, a taxable value and a tax split.
--
-- One table pair, one read contract, instead of eleven.
-- =============================================================================

-- --- Direction --------------------------------------------------------------
-- The axis every output-tax and ITC question turns on first, and it is NOT
-- derivable from the counterparty column: a Bill of Entry has no GSTIN at all,
-- and an inter-GSTIN transfer has one on both sides.
--
-- Stored on the Silver row rather than joined from the registry at read time.
-- That is a deliberate denormalisation, and the reason is bitemporal rather
-- than performance: the row records the direction we understood the document to
-- have WHEN WE INGESTED IT (ARCHITECTURE.md 8). If a registry contract is later
-- corrected, historical rows must keep answering "what did we believe when we
-- released that report" — which a live join would silently rewrite.
INSERT INTO platform_ref.universal_master (value_type, value, description) VALUES
    ('Direction', 'INWARD',   'Inward supply — the entity is the recipient. ITC side.'),
    ('Direction', 'OUTWARD',  'Outward supply — the entity is the supplier. Output tax side.'),
    ('Direction', 'INTERNAL', 'Neither — movement within the entity or its GSTINs. '
                              'Job-work dispatch and stock transfer, where no supply arises '
                              'but the movement is still evidenced.')
ON CONFLICT (value_type, value) DO NOTHING;


-- --- Artefact type vs row type: the decision --------------------------------
-- registry/README.md flagged this and TODO.md required it settled before
-- Archetype 1 was built. Settling it here because the archetype table's FK
-- forces the question: transaction_document.doc_type references
-- platform_ref.document_type, so it must carry a REGISTRY code.
--
--   Artefact type  what arrives.        A1.03 PURCHASE_REGISTER — one file.
--   Row type       what comes out.      Many purchase invoices.
--
-- DECIDED: the registry carries artefact types only. A row's character is
-- expressed by (doc_type, direction) on the archetype table, not by a second
-- vocabulary. `PURCHASE_REGISTER` + `INWARD` says everything `PURCHASE_INVOICE`
-- said, and says it in the vocabulary the Source Document Register already
-- uses — so the CA team's refs and the platform's codes stay one namespace.
--
-- The alternative — growing the registry to hold row types too — would mean
-- every archetype declaring which row types it emits, and the T9 completeness
-- gate having to know that a request for one implies the other. That is two
-- vocabularies describing overlapping things, which is what this repo already
-- has and is what the finding objected to.
--
-- The five Phase 1 values are RETIRED FROM USE, not deleted. Deletion is not
-- available: bronze.artefact_ledger rows already reference them and Bronze is
-- INSERT-only (README invariant 6), so history must stay resolvable. They are
-- marked instead, and tests/conformance/test_registry.py asserts no source file
-- emits one — a rename that leaves a literal behind is caught there rather than
-- by a foreign key violation in production.
UPDATE platform_ref.universal_master
   SET description = 'RETIRED row type — use the registry artefact type. '
                     'Retained because Bronze is insert-only and existing '
                     'artefact_ledger rows reference this value. Migration 008.'
 WHERE value_type = 'Document_Type'
   AND value IN ('PURCHASE_INVOICE', 'SALES_INVOICE', 'CREDIT_NOTE',
                 'DEBIT_NOTE', 'PAYMENT_RECORD');
