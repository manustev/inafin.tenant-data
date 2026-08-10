"""The 23 flat Group A1 registers, declared.

Transcribed from `migrations/tenant/010_a1_registers.sql`, which took its
business columns verbatim from `reference/inafin_a1_schema.sql`. A1.01
SALES_REGISTER is absent on purpose — it has a header/line split and its own
hand-written loader (`src/silver/sales_register.py`), which is the reviewed
pattern these follow rather than replace.

**Read the DDL, not this file, when you want to know what a column means.** This
is a projection of the table for the loader's benefit, kept honest by
`tests/conformance/test_register_specs.py`, which compares every entry below
against the live schema. If the two disagree the table wins and the gate fails.

`required` is derived by one rule — NOT NULL and no DEFAULT — so it is
mechanical, not a judgement. `valid_from_column` is a judgement: it is set only
where the register has a NOT NULL business date, and left None everywhere else
so a legitimately dateless row cannot crash on `valid_from NOT NULL`.
"""

from __future__ import annotations

from src.silver.registers.spec import Column as Col
from src.silver.registers.spec import KeyKind, Kind, RegisterSpec

_T = Kind.TEXT
_D = Kind.DATE
_N = Kind.DECIMAL
_I = Kind.INTEGER
_B = Kind.BOOLEAN


SPECS: tuple[RegisterSpec, ...] = (
    # --- A1.02 CREDIT_DEBIT_NOTE_REGISTER -----------------------------------
    RegisterSpec(
        doc_type_codes=("CREDIT_DEBIT_NOTE_REGISTER",),
        table="credit_debit_note_register",
        period_column="tax_period",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "note_no"),
        valid_from_column="note_date",
        columns=(
            Col("note_type", _T, required=True),
            Col("note_no", _T, required=True),
            Col("note_date", _D, required=True),
            Col("original_invoice_no", _T, required=True),
            Col("customer_gstin", _T),
            Col("supply_type", _T),
            Col("reason_code", _T),
            Col("reason_text", _T),
            Col("adjusted_taxable_value", _N, required=True),
            Col("gst_rate", _N),
            Col("cgst", _N),
            Col("sgst", _N),
            Col("igst", _N),
            Col("cess", _N),
            Col("note_value", _N),
        ),
    ),
    # --- A1.03 PURCHASE_REGISTER --------------------------------------------
    # Step 4 of TYPED-TABLES-PLAN.md 10 redefines v1_purchase_invoice over this
    # table and retires the archetype path. That is a separate migration; this
    # loader does not touch transaction_document.
    RegisterSpec(
        doc_type_codes=("PURCHASE_REGISTER",),
        table="purchase_register",
        period_column="tax_period",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "supplier_gstin", "invoice_no"),
        valid_from_column="invoice_date",
        columns=(
            Col("supplier_gstin", _T),
            Col("invoice_no", _T, required=True),
            Col("invoice_date", _D, required=True),
            Col("hsn_sac", _T),
            Col("taxable_value", _N, required=True),
            Col("gst_rate", _N),
            Col("cgst", _N),
            Col("sgst", _N),
            Col("igst", _N),
            Col("cess", _N),
            Col("gl_code", _T, required=True),
            Col("cost_centre", _T, required=True),
            Col("itc_eligibility", _T),
            Col("rcm_flag", _B),
        ),
    ),
    # --- A1.04 ADVANCE_RECEIPT_REGISTER -------------------------------------
    RegisterSpec(
        doc_type_codes=("ADVANCE_RECEIPT_REGISTER",),
        table="advance_receipt_register",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        valid_from_column="receipt_date",
        columns=(
            Col("receipt_no", _T),
            Col("receipt_date", _D, required=True),
            Col("amount", _N, required=True),
            Col("customer", _T),
            Col("customer_gstin", _T),
            Col("supply_type", _T),
            Col("gst_rate", _N),
            Col("gst_paid_on_advance", _N),
            Col("invoice_linkage", _T),
            Col("is_adjusted", _B),
        ),
    ),
    # --- A1.05 UNBILLED_REVENUE_SCHEDULE ------------------------------------
    RegisterSpec(
        doc_type_codes=("UNBILLED_REVENUE_SCHEDULE",),
        table="unbilled_revenue_schedule",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        columns=(
            Col("item_id", _T),
            Col("description", _T),
            Col("contract_ref", _T),
            Col("customer", _T),
            Col("customer_gstin", _T),
            Col("amount", _N, required=True),
            Col("date_of_service_completion", _D),
        ),
    ),
    # --- A1.06 JOBWORK_DISPATCH_REGISTER ------------------------------------
    RegisterSpec(
        doc_type_codes=("JOBWORK_DISPATCH_REGISTER",),
        table="job_work_dispatch_register",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        valid_from_column="date_of_goods_dispatch_to_job_worker",
        columns=(
            Col("challan_no", _T),
            Col("date_of_goods_dispatch_to_job_worker", _D, required=True),
            Col("description", _T),
            Col("hsn", _T),
            Col("qty", _N),
            Col("uom", _T),
            Col("taxable_value", _N),
            Col("job_worker_gstin", _T),
            Col("expected_return_date", _D),
        ),
    ),
    # --- A1.07 CONTINUOUS_SUPPLY_CONTRACT_REGISTER --------------------------
    RegisterSpec(
        doc_type_codes=("CONTINUOUS_SUPPLY_CONTRACT_REGISTER",),
        table="running_account_contract_register",
        period_column="tax_period",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "contract_ref"),
        columns=(
            Col("contract_ref", _T, required=True),
            Col("customer", _T),
            Col("customer_gstin", _T),
            Col("billing_frequency", _T),
            Col("statement_date", _D),
            Col("delivery_date", _D),
            Col("amount", _N),
        ),
    ),
    # --- A1.08 AUDITED_PROFIT_AND_LOSS --------------------------------------
    RegisterSpec(
        doc_type_codes=("AUDITED_PROFIT_AND_LOSS",),
        table="audited_pl_account",
        period_column="fy",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "fy", "row_hash"),
        columns=(
            Col("gl_code", _T),
            Col("gl_account", _T, required=True),
            Col("narration", _T),
            Col("account_type", _T),
            Col("amount", _N, required=True),
            Col("dr_cr", _T),
            Col("gst_treatment", _T),
        ),
    ),
    # --- A1.09 TRIAL_BALANCE ------------------------------------------------
    RegisterSpec(
        doc_type_codes=("TRIAL_BALANCE",),
        table="trial_balance",
        period_column="fy",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "gl_code"),
        columns=(
            Col("gl_code", _T, required=True),
            Col("account_description", _T, required=True),
            Col("opening_balance", _N),
            Col("closing_balance", _N),
            Col("dr_cr", _T),
            Col("gst_treatment", _T),
        ),
    ),
    # --- A1.10 BALANCE_SHEET ------------------------------------------------
    RegisterSpec(
        doc_type_codes=("BALANCE_SHEET",),
        table="balance_sheet",
        period_column="fy",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "line_item"),
        columns=(
            Col("line_item", _T, required=True),
            Col("gl_code", _T),
            Col("category", _T),
            Col("amount", _N, required=True),
        ),
    ),
    # --- A1.11 FIXED_ASSET_REGISTER -----------------------------------------
    RegisterSpec(
        doc_type_codes=("FIXED_ASSET_REGISTER",),
        table="fixed_asset_register",
        period_column="tax_period",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "asset_id"),
        columns=(
            Col("asset_id", _T, required=True),
            Col("description", _T),
            Col("hsn", _T),
            Col("purchase_date", _D),
            Col("purchase_value", _N),
            Col("itc_availed", _N),
            Col("useful_life_months", _I),
            Col("date_of_disposal", _D),
            Col("disposal_value", _N),
            Col("disposal_type", _T),
        ),
    ),
    # --- A1.12 CREDITOR_AGEING_REPORT ---------------------------------------
    RegisterSpec(
        doc_type_codes=("CREDITOR_AGEING_REPORT",),
        table="creditor_ageing_report",
        period_column="tax_period",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "as_at_date", "supplier_gstin", "invoice_no"),
        valid_from_column="as_at_date",
        columns=(
            Col("supplier_gstin", _T),
            Col("supplier_name", _T),
            Col("invoice_no", _T, required=True),
            Col("invoice_date", _D, required=True),
            Col("outstanding_amount", _N, required=True),
            Col("ageing_days", _I),
            Col("ageing_bucket", _T),
            Col("as_at_date", _D, required=True),
        ),
    ),
    # --- A1.13 SUPPLIER_PAYMENT_REGISTER ------------------------------------
    RegisterSpec(
        doc_type_codes=("SUPPLIER_PAYMENT_REGISTER",),
        table="payment_register",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        valid_from_column="payment_date",
        columns=(
            Col("payment_date", _D, required=True),
            Col("amount", _N, required=True),
            Col("supplier_gstin", _T),
            Col("supplier_name", _T),
            Col("invoice_no", _T),
            Col("payment_ref", _T),
            Col("payment_mode", _T),
        ),
    ),
    # --- A1.14 BANK_STATEMENT_OUTWARD ---------------------------------------
    RegisterSpec(
        doc_type_codes=("BANK_STATEMENT_OUTWARD",),
        table="bank_statement_outward",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        valid_from_column="txn_date",
        columns=(
            Col("txn_date", _D, required=True),
            Col("amount", _N, required=True),
            Col("beneficiary", _T),
            Col("beneficiary_account", _T),
            Col("narration", _T),
            Col("bank_ref", _T),
            Col("invoice_ref", _T),
        ),
    ),
    # --- A1.15 BANK_STATEMENT_INWARD_FX -------------------------------------
    RegisterSpec(
        doc_type_codes=("BANK_STATEMENT_INWARD_FX",),
        table="bank_statement_inward_forex",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        valid_from_column="txn_date",
        columns=(
            Col("txn_date", _D, required=True),
            Col("amount_fcy", _N, required=True),
            Col("currency", _T, required=True),
            Col("amount_inr", _N),
            Col("remitter", _T),
            Col("ad_bank_ref", _T),
            Col("export_invoice_ref", _T),
            Col("firc_no", _T),
        ),
    ),
    # --- A1.16 STOCK_REGISTER -----------------------------------------------
    RegisterSpec(
        doc_type_codes=("STOCK_REGISTER",),
        table="stock_inventory_register",
        period_column="tax_period",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "tax_period", "sku"),
        columns=(
            Col("sku", _T, required=True),
            Col("description", _T),
            Col("hsn", _T),
            Col("opening_qty", _N),
            Col("closing_qty", _N),
            Col("write_off_qty", _N),
            Col("write_off_value", _N),
        ),
    ),
    # --- A1.17 SKU_MASTER ---------------------------------------------------
    RegisterSpec(
        doc_type_codes=("SKU_MASTER",),
        table="product_sku_master",
        period_column="tax_period",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "sku"),
        columns=(
            Col("sku", _T, required=True),
            Col("description", _T),
            Col("hsn", _T),
            Col("mrp_applicable", _B),
            Col("declared_mrp", _N),
        ),
    ),
    # --- A1.18 INTER_GSTIN_TRANSACTION_REGISTER -----------------------------
    RegisterSpec(
        doc_type_codes=("INTER_GSTIN_TRANSACTION_REGISTER",),
        table="inter_gstin_transaction_register",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        valid_from_column="txn_date",
        columns=(
            Col("from_gstin", _T, required=True),
            Col("to_gstin", _T, required=True),
            Col("txn_type", _T),
            Col("txn_date", _D, required=True),
            Col("invoice_no", _T),
            Col("description", _T),
            Col("hsn_sac", _T),
            Col("taxable_value", _N, required=True),
            Col("gst_rate", _N),
            Col("cgst", _N),
            Col("sgst", _N),
            Col("igst", _N),
        ),
    ),
    # --- A1.19 COST_ALLOCATION_REGISTER -------------------------------------
    RegisterSpec(
        doc_type_codes=("COST_ALLOCATION_REGISTER",),
        table="cost_allocation_register",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        columns=(
            Col("from_gstin", _T),
            Col("to_gstin", _T),
            Col("allocation_basis", _T),
            Col("description", _T),
            Col("amount", _N, required=True),
            Col("has_invoice", _B),
            Col("invoice_no", _T),
        ),
    ),
    # --- A1.20 + D1.01-D1.07 PLATFORM_SETTLEMENT_REPORT ---------------------
    # The one table seven registry codes share. Shared migration 013 relaxed the
    # unique index on document_type.table_name for it and records why nothing
    # else in the 125 qualifies. `doc_type_code` therefore has no default and the
    # caller must name the operator's report; `report_type` is a separate,
    # narrower discriminator that comes from the file.
    RegisterSpec(
        doc_type_codes=(
            "AMAZON_MTR",
            "FLIPKART_GTR",
            "MARKETPLACE_SETTLEMENT_SSR",
            "MARKETPLACE_DISBURSEMENT_DRR",
            "MARKETPLACE_RETURNS_VRET",
            "FOOD_DELIVERY_PARTNER_REPORT",
            "OTHER_MARKETPLACE_SETTLEMENT",
        ),
        table="platform_settlement_report",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        columns=(
            Col("report_type", _T, required=True),
            Col("operator", _T, required=True),
            Col("order_no", _T),
            Col("order_date", _D),
            Col("invoice_no", _T),
            Col("invoice_date", _D),
            Col("buyer_state", _T),
            Col("taxable_value", _N),
            Col("gst_rate", _N),
            Col("cgst", _N),
            Col("sgst", _N),
            Col("igst", _N),
            Col("tcs", _N),
            Col("marketplace_fee", _N),
            Col("shipping_charges", _N),
            Col("net_payout", _N),
        ),
    ),
    # --- A1.21 ITC_REVERSAL_REGISTER ----------------------------------------
    RegisterSpec(
        doc_type_codes=("ITC_REVERSAL_REGISTER",),
        table="itc_reversal_register",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        columns=(
            Col("reversal_type", _T, required=True),
            Col("reversal_basis", _T),
            Col("cgst_reversed", _N),
            Col("sgst_reversed", _N),
            Col("igst_reversed", _N),
            Col("cess_reversed", _N),
            Col("remarks", _T),
        ),
    ),
    # --- A1.22 RCM_REGISTER -------------------------------------------------
    RegisterSpec(
        doc_type_codes=("RCM_REGISTER",),
        table="rcm_register",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        columns=(
            Col("rcm_category", _T, required=True),
            Col("supplier_gstin", _T),
            Col("supplier_name", _T),
            Col("invoice_no", _T),
            Col("invoice_date", _D),
            Col("taxable_value", _N, required=True),
            Col("gst_rate", _N),
            Col("rcm_liability", _N),
            Col("cgst", _N),
            Col("sgst", _N),
            Col("igst", _N),
            Col("paid_period", _D),
            Col("is_paid", _B),
        ),
    ),
    # --- A1.23 FOREIGN_CURRENCY_PAYMENT_REGISTER ----------------------------
    RegisterSpec(
        doc_type_codes=("FOREIGN_CURRENCY_PAYMENT_REGISTER",),
        table="foreign_currency_payment_register",
        period_column="tax_period",
        key_kind=KeyKind.CONTENT,
        key_columns=("entity_id", "gstin", "tax_period", "row_hash"),
        valid_from_column="payment_date",
        columns=(
            Col("payment_date", _D, required=True),
            Col("amount_fcy", _N, required=True),
            Col("currency", _T, required=True),
            Col("amount_inr", _N),
            Col("vendor", _T),
            Col("country", _T),
            Col("purpose", _T),
            Col("invoice_contract_ref", _T),
        ),
    ),
    # --- A1.24 HO_COMMON_INPUT_SERVICE_INVOICE ------------------------------
    RegisterSpec(
        doc_type_codes=("HO_COMMON_INPUT_SERVICE_INVOICE",),
        table="common_input_service_invoice",
        period_column="tax_period",
        key_kind=KeyKind.NATURAL,
        key_columns=("entity_id", "gstin", "supplier_gstin", "invoice_no"),
        valid_from_column="invoice_date",
        columns=(
            Col("invoice_no", _T, required=True),
            Col("invoice_date", _D, required=True),
            Col("supplier_gstin", _T),
            Col("service_category", _T),
            Col("taxable_value", _N, required=True),
            Col("gst_rate", _N),
            Col("cgst", _N),
            Col("sgst", _N),
            Col("igst", _N),
        ),
    ),
)


SPEC_BY_TABLE: dict[str, RegisterSpec] = {s.table: s for s in SPECS}

# Keyed by registry code, so the caller looks a spec up by the thing Bronze
# declared (`artefact_ledger.declared_document_type`) rather than by a table name
# it would otherwise have to know. Seven codes map to platform_settlement_report.
SPEC_BY_DOC_TYPE: dict[str, RegisterSpec] = {
    code: spec for spec in SPECS for code in spec.doc_type_codes
}


def spec_for(doc_type_code: str) -> RegisterSpec:
    try:
        return SPEC_BY_DOC_TYPE[doc_type_code]
    except KeyError:
        raise KeyError(
            f"{doc_type_code!r} has no flat-register loader."
            " SALES_REGISTER has its own (src/silver/sales_register.py);"
            " everything else outside Group A1 is not built yet."
        ) from None
