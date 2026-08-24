"""B2 to B6 - registration, proceedings, e-invoice/EWB, ICEGATE, DGFT."""
import datetime as dt, random, hashlib
from core import *

R = random.Random(101)

# ============================================================ shipping bills
def prepare_shipping_bills():
    """Attach shipping bill / EGM / realisation attributes to export invoices.
    Must run BEFORE the GSTR-1 emitter so SB details flow into Table 6A."""
    exports = [o for o in UNI.out if o["cat"] == "EXPWOP"]
    for n, o in enumerate(exports, start=1):
        sb_dt = o["idt"] + dt.timedelta(days=R.randint(1, 9))
        o["sbnum"] = f"{7100000 + n*13:07d}"
        o["sbdt"] = ddmmyyyy(sb_dt)
        o["sb_date_obj"] = sb_dt
        o["port"] = "INBLR4" if n % 3 else "INMAA1"
        o["fob"] = o["txval"]
        o["egm"] = "Filed"
        o["egm_dt"] = ddmmyyyy(sb_dt + dt.timedelta(days=R.randint(2, 12)))
        o["fx_rate"] = round(R.uniform(83.1, 87.4), 2) if o["currency"] == "USD" else round(R.uniform(89.2, 94.6), 2)
        o["fob_fc"] = r2(o["txval"] / o["fx_rate"])
        o["realised"] = True
    # defects
    exports[4]["egm"] = "Not Filed"; exports[4]["egm_dt"] = ""
    exports[4]["mock_flag"] = "EGM not filed - refund cannot be processed"
    exports[9]["fob"] = r2(exports[9]["txval"] * 1.062)
    exports[9]["mock_flag"] = "Shipping bill FOB value exceeds GSTR-1 Table 6A invoice value by 6.2%"
    exports[2]["realised"] = False
    exports[2]["mock_flag"] = "No FIRC/eBRC - export proceeds unrealised beyond 12 months"
    exports[7]["sbnum"] = ""; exports[7]["sbdt"] = ""
    exports[7]["mock_flag"] = "Shipping bill reference missing in GSTR-1 Table 6A"
    anomaly("B5.02-EGM", ["B5.01", "B5.02"], "FY2024-25",
            f"Shipping bill for export invoice {exports[4]['inum']} has EGM not filed.",
            "EGM status check - IGST refund / LUT export validation blocked until EGM filed.")
    anomaly("B5.01-FOB", ["B1.01", "B5.01"], "FY2024-25",
            f"FOB value on shipping bill for {exports[9]['inum']} exceeds GSTR-1 Table 6A value by 6.2%.",
            "Three-way match: sales invoice vs GSTR-1 Table 6A vs ICEGATE shipping bill.")
    anomaly("B6.01-BRC", ["B6.01", "D2.01"], "FY2024-25",
            f"Export invoice {exports[2]['inum']} has no eBRC/FIRC - realisation beyond 12 months.",
            "Rule 96A: IGST + interest payable if proceeds unrealised in 12 months absent RBI extension.")
    anomaly("B5.01-SBMISS", ["B1.01", "B5.01"], "FY2024-25",
            f"GSTR-1 Table 6A for {exports[7]['inum']} carries no shipping bill number.",
            "Invoice-to-shipping-bill linkage missing; ICEGATE-GSTN data flow will not validate.")


# ================================================ B2 registration and status
def b2_registration():
    base = "B2_Registration_and_Status"
    regs = []
    for g, m in ENTITY_GSTINS.items():
        regs.append(dict(
            gstin=g, legal_name=LEGAL_NAME, trade_name=TRADE_NAME, pan=PAN,
            constitution="Private Limited Company", registration_type=m["rtype"],
            state_code=m["state"], state=m["state_name"],
            date_of_liability=m["eff"], effective_date_of_registration=m["eff"],
            certificate_no=f"REG-06/{m['state']}/{PAN}/{g[12]}",
            principal_place_of_business=m["addr"],
            additional_places=[{"address": "Warehouse 3, Nelamangala Road, Bengaluru 562123",
                                "nature": "Warehouse / Depot"}] if g == GSTIN_KA else [],
            nature_of_business=["Manufacturing", "Wholesale Business", "Export"],
            authorised_signatories=[dict(name="RAJEEV MENON", designation="Director",
                                         pan="AFVPM7788L", status="Active"),
                                    dict(name="SUNITHA RAO", designation="Company Secretary",
                                         pan="BKQPR2211H", status="Active")],
            jurisdiction_centre="Bengaluru North Commissionerate, Division IV, Range AD" if m["state"] == "29"
            else "Navi Mumbai Commissionerate, Division II, Range IV",
            jurisdiction_state=f"{m['state_name']} - LGSTO 070" if m["state"] == "29" else f"{m['state_name']} - Ward 402",
            status="Active", cancellation_date=None,
            aggregate_turnover_preceding_fy=1284000000.0,
            composition_opted="No", ewb_enabled="Yes",
            einvoice_applicable="Yes", einvoice_applicable_from="01-08-2023"))
    write_json(f"{base}/B2.01_REG-06_Taxpayer/REG06_taxpayer_all_GSTINs.json", regs)
    write_csv(f"{base}/B2.01_REG-06_Taxpayer/REG06_taxpayer_all_GSTINs.csv",
              [{k: (v if not isinstance(v, (list, dict)) else str(v)) for k, v in r.items()} for r in regs])

    def party_rows(parties, kind):
        out = []
        for p in parties:
            out.append(dict(gstin=p["gstin"], legal_name=p["name"], trade_name=p["name"].title(),
                            party_type=kind, pan=p["gstin"][2:12], state_code=p["state"],
                            registration_type="Composition" if p["rtype"] == "Composition"
                            else ("Input Service Distributor" if p["rtype"] == "ISD" else
                                  ("SEZ Unit" if p["rtype"] == "SEZ" else "Regular")),
                            effective_date_of_registration="01-07-2017",
                            status=p.get("status", "Active"),
                            cancellation_date=p.get("cancel_dt", ""),
                            cancellation_type="Suo-moto (retrospective)" if p.get("cancel_dt") else "",
                            filing_status_last_6_returns="Regular" if not p.get("cancel_dt") else "Defaulter",
                            notes=p.get("note", "")))
        return out

    write_csv(f"{base}/B2.02_REG-06_Customers/customer_registration_status.csv",
              party_rows(CUSTOMERS, "Customer"))
    write_csv(f"{base}/B2.03_REG-06_Suppliers/supplier_registration_status.csv",
              party_rows(SUPPLIERS, "Supplier"))

    # B2.04 amendment history
    amd = [
        dict(gstin=GSTIN_KA, amendment_type="Core - Additional place of business",
             field="Additional place of business", old_value="",
             new_value="Warehouse 3, Nelamangala Road, Bengaluru 562123",
             application_ref="REG-14", arn="AA290724091234K", date_of_application="09-07-2024",
             date_of_approval="19-07-2024", effective_date="09-07-2024"),
        dict(gstin=GSTIN_KA, amendment_type="Non-core - Authorised signatory",
             field="Authorised signatory", old_value="P. NARAYANAN",
             new_value="SUNITHA RAO", application_ref="REG-14", arn="AA291124077781B",
             date_of_application="14-11-2024", date_of_approval="14-11-2024",
             effective_date="14-11-2024"),
        dict(gstin=GSTIN_TN, amendment_type="New registration",
             field="Registration", old_value="", new_value="Regular",
             application_ref="REG-01", arn="AA330824055512C", date_of_application="02-08-2024",
             date_of_approval="12-08-2024", effective_date="12-08-2024"),
    ]
    write_csv(f"{base}/B2.04_Registration_Amendment_History/registration_amendment_history.csv", amd)

    # B2.05 composition opt in / out (customer GOKUL TRADERS)
    comp = [dict(gstin=CUSTOMERS[6]["gstin"], legal_name=CUSTOMERS[6]["name"],
                 form="CMP-02", event="Opt-in to composition scheme", financial_year="2023-24",
                 arn="AA290323044412D", date_of_filing="28-03-2023", effective_date="01-04-2023"),
            dict(gstin=CUSTOMERS[6]["gstin"], legal_name=CUSTOMERS[6]["name"],
                 form="CMP-04", event="Withdrawal from composition scheme", financial_year="2024-25",
                 arn="AA291024033398E", date_of_filing="05-10-2024", effective_date="01-10-2024")]
    write_csv(f"{base}/B2.05_Composition_Optin_Optout/composition_scheme_events.csv", comp)

    # B2.06 ISD registration certificate
    write_json(f"{base}/B2.06_ISD_Registration/ISD_registration_certificate.json", dict(
        gstin=GSTIN_ISD, legal_name=LEGAL_NAME, trade_name=TRADE_NAME, pan=PAN,
        registration_type="Input Service Distributor", state="Karnataka", state_code="29",
        effective_date_of_registration="01-10-2023",
        date_of_liability="01-10-2023",
        principal_place_of_business=ENTITY_GSTINS[GSTIN_ISD]["addr"],
        status="Active",
        statutory_basis="Section 20 CGST Act read with Rule 39; mandatory w.e.f. 01-10-2023 "
                        "(Finance Act 2023) where common input services are received at HO",
        recipient_gstins=[GSTIN_KA, GSTIN_MH, GSTIN_TN]))

    # B2.07 GSTIN status at date - the API most reconciliation code calls
    rows = []
    for p in CUSTOMERS + SUPPLIERS:
        canc = p.get("cancel_dt")
        rows.append(dict(gstin=p["gstin"], legal_name=p["name"], party_type="",
                         status_current="Cancelled" if canc else "Active",
                         cancellation_effective_date=canc or "",
                         suspension_date="", registration_type=p["rtype"],
                         state_code=p["state"],
                         status_as_on_01_04_2024="Active",
                         status_as_on_30_09_2024="Active",
                         status_as_on_31_03_2025="Cancelled" if canc else "Active",
                         api_source="GSTN Search Taxpayer API (Track application/Search by GSTIN)",
                         last_verified="15-04-2025"))
    write_csv(f"{base}/B2.07_GSTIN_Status_at_Date/gstin_status_at_date.csv", rows)


# ================================================ B3 notices and proceedings
def b3_notices():
    base = "B3_Notices_Proceedings_Settlements"
    scn = [
        dict(notice_no="ZD290920240012345", form="DRC-01 (SCN)", gstin=GSTIN_KA,
             issue_date="18-09-2024", period_from="04-2021", period_to="03-2022",
             issue="Excess ITC availed vs GSTR-2A for FY 2021-22 - Section 16(2)(aa)",
             section_invoked="74", tax_demanded=4820000.0, interest=1928000.0, penalty=4820000.0,
             total_demanded=11568000.0, current_stage="Reply filed - personal hearing scheduled",
             reply_due_date="18-10-2024", reply_filed_date="16-10-2024",
             officer="Assistant Commissioner, Division IV, Bengaluru North"),
        dict(notice_no="ZD290120250067890", form="DRC-01 (SCN)", gstin=GSTIN_KA,
             issue_date="22-01-2025", period_from="04-2022", period_to="03-2023",
             issue="Non-payment of RCM on import of services - Section 9(3) / Notification 10/2017",
             section_invoked="73", tax_demanded=1345000.0, interest=498000.0, penalty=134500.0,
             total_demanded=1977500.0, current_stage="Reply under preparation",
             reply_due_date="21-02-2025", reply_filed_date="",
             officer="Superintendent, Range AD, Division IV, Bengaluru North"),
        dict(notice_no="ZA270620240054321", form="ASMT-10 (Scrutiny)", gstin=GSTIN_MH,
             issue_date="11-06-2024", period_from="04-2022", period_to="03-2023",
             issue="GSTR-1 vs GSTR-3B outward liability difference",
             section_invoked="61", tax_demanded=286400.0, interest=91600.0, penalty=0.0,
             total_demanded=378000.0, current_stage="ASMT-11 reply accepted - ASMT-12 issued",
             reply_due_date="11-07-2024", reply_filed_date="08-07-2024",
             officer="State Tax Officer, Ward 402, Navi Mumbai"),
    ]
    write_csv(f"{base}/B3.01_Open_SCN_Register/open_scn_register.csv", scn)
    write_json(f"{base}/B3.01_Open_SCN_Register/open_scn_register.json", scn)

    drc01b = [dict(reference_no="DRC01B/29/202409/00417", form="DRC-01B Part A", gstin=GSTIN_KA,
                   tax_period="092024", issue_date="26-11-2024",
                   liability_declared_in_gstr1=None, liability_paid_in_gstr3b=None,
                   difference=None, threshold_breached="Yes - difference exceeds the parameters notified under Rule 88C",
                   response_form="DRC-01B Part B", response_due_date="26-12-2024",
                   response_filed="No", status="Open",
                   remark="System generated intimation of difference between liability declared in "
                          "GSTR-1 and liability paid in GSTR-3B. Amounts to be filled from the "
                          "generated GSTR-1/GSTR-3B pair for 092024.")]
    write_json(f"{base}/B3.02_DRC-01B_Notices/drc01b_notices.json", drc01b)

    drc03 = [dict(arn="AD290824000123X", form="DRC-03", gstin=GSTIN_KA, payment_date="12-08-2024",
                  cause_of_payment="Voluntary", period_from="04-2023", period_to="03-2024",
                  section="73(5)", issue="ITC reversal under Rule 42 - short reversal on exempt turnover",
                  tax_igst=0.0, tax_cgst=412000.0, tax_sgst=412000.0, interest=148300.0, penalty=0.0,
                  total_paid=972300.0, ledger_utilised="Cash ledger",
                  linked_notice=""),
             dict(arn="AD290225000456Y", form="DRC-03", gstin=GSTIN_KA, payment_date="27-02-2025",
                  cause_of_payment="SCN / Voluntary against notice", period_from="04-2021",
                  period_to="03-2022", section="74(5)",
                  issue="Part payment against SCN ZD290920240012345 - excess ITC FY 2021-22",
                  tax_igst=1500000.0, tax_cgst=0.0, tax_sgst=0.0, interest=612000.0, penalty=0.0,
                  total_paid=2112000.0, ledger_utilised="Cash ledger",
                  linked_notice="ZD290920240012345")]
    write_csv(f"{base}/B3.03_DRC-03_Voluntary_Payments/drc03_voluntary_payments.csv", drc03)

    amnesty = [dict(scheme="Section 128A Amnesty (waiver of interest and penalty) - SPL-02 order",
                    gstin=GSTIN_KA, application_form="SPL-01", application_arn="AM290325000078Z",
                    application_date="14-03-2025", order_no="SPL-02/29/2025/0091",
                    order_date="30-05-2025", period_from="07-2017", period_to="03-2020",
                    issue_settled="Excess ITC and outward liability shortfall FY 2017-18 to FY 2019-20",
                    tax_paid=3860000.0, interest_waived=2914000.0, penalty_waived=386000.0,
                    status="Settled - liability closed for the covered period and issue")]
    write_csv(f"{base}/B3.04_Amnesty_Settlement/amnesty_settlement_orders.csv", amnesty)

    court = [dict(case_no="WP 21874/2024", forum="High Court of Karnataka", gstin=GSTIN_KA,
                  petitioner=LEGAL_NAME, respondent="Union of India and Others",
                  order_date="04-12-2024", order_type="Interim stay",
                  demand_reference="ZD290920240012345",
                  issue_stayed="Recovery of tax, interest and penalty on excess ITC FY 2021-22",
                  amount_stayed=11568000.0, conditions="Pre-deposit of 10% of tax demanded",
                  validity="Until further orders", current_status="Stay operative",
                  next_hearing="21-08-2025")]
    write_csv(f"{base}/B3.05_Court_Stay_Orders/court_stay_orders.csv", court)

    hearing = [dict(reference_no="PH/DIV4/2024/0339", gstin=GSTIN_KA, form="Personal Hearing Notice",
                    linked_notice="ZD290920240012345", hearing_date="27-11-2024",
                    hearing_mode="Virtual", authority="Assistant Commissioner, Division IV",
                    outcome="Adjourned at taxpayer request", next_date="18-12-2024"),
               dict(reference_no="ORD/DIV4/2025/0071", gstin=GSTIN_KA, form="Adjudication Order (DRC-07)",
                    linked_notice="ZA270620240054321", hearing_date="12-02-2025",
                    hearing_mode="Physical", authority="State Tax Officer, Ward 402, Navi Mumbai",
                    outcome="Demand confirmed in part - Rs.1,86,400 tax and Rs.62,300 interest",
                    next_date="")]
    write_csv(f"{base}/B3.06_Hearing_and_Adjudication_Orders/hearing_and_adjudication_orders.csv", hearing)

    rfd = []
    for i, (per, amt) in enumerate([("042024-062024", 8420000.0), ("072024-092024", 7135000.0),
                                    ("102024-122024", 9210000.0), ("012025-032025", 6890000.0)], 1):
        filed = ["18-07-2024", "22-10-2024", "19-01-2025", "24-04-2025"][i - 1]
        rfd.append(dict(arn=f"AA2904250{i:05d}R", form="RFD-01", gstin=GSTIN_KA,
                        refund_type="Refund of unutilised ITC - export of goods under LUT (Rule 89(4))",
                        period=per, amount_claimed=amt, date_of_filing=filed,
                        acknowledgement_rfd02=("Issued" if i < 4 else "Pending"),
                        deficiency_memo_rfd03=("Issued 06-02-2025 - shipping bill / GSTR-1 Table 6A "
                                              "value mismatch" if i == 3 else ""),
                        provisional_sanction_rfd04=(r2(amt * 0.9) if i < 3 else 0.0),
                        final_order_rfd06=(r2(amt * 0.97) if i < 3 else 0.0),
                        amount_rejected=(r2(amt * 0.03) if i < 3 else 0.0),
                        status=("Sanctioned" if i < 3 else ("Deficiency memo issued" if i == 3
                                                            else "Filed - under processing")),
                        days_pending=(0 if i < 3 else 74)))
    write_csv(f"{base}/B3.07_Refund_Applications/rfd01_refund_applications.csv", rfd)
    anomaly("B3.07-RFD-60DAY", ["B3.07"], "102024-122024",
            "RFD-01 for Q3 FY2024-25 has a deficiency memo (RFD-03) and has crossed the 60-day "
            "processing window.",
            "60-day refund processing deadline monitoring and interest u/s 56.")


# ============================================== B4 e-invoice and e-way bill
def b4_einvoice_ewb():
    base = "B4_EInvoice_and_EWayBill"
    irn_rows, ewb_rows = [], []
    skip_irn, skip_ewb = set(), set()
    einv_eligible = [o for o in UNI.out if o["cat"] in ("B2B", "SEZWOP", "EXPWOP") and o["inum"]]
    for idx in (11, 87, 154):
        skip_irn.add(einv_eligible[idx]["inum"])
    goods = [o for o in UNI.out if o["inum"] and o["hsn"] and not o["hsn"].startswith("99")
             and o["val"] > 50000]
    for idx in (23, 96):
        skip_ewb.add(goods[idx]["inum"])

    for o in einv_eligible:
        if o["inum"] in skip_irn:
            continue
        ack_dt = dt.datetime.combine(o["idt"], dt.time(R.randint(9, 19), R.randint(0, 59)))
        payload = f"{o['gstin']}{o['inum']}{o['idt']}".encode()
        irn_rows.append(dict(
            gstin=o["gstin"], irn=hashlib.sha256(payload).hexdigest(),
            ack_no=f"1{R.randint(10**13, 10**14-1)}",
            ack_date=ack_dt.strftime("%d-%m-%Y %H:%M:%S"),
            document_type="INV", invoice_no=o["inum"], invoice_date=ddmmyyyy(o["idt"]),
            recipient_gstin=o["ctin"] or "URP", pos=o["pos"],
            supply_type={"B2B": "B2B", "SEZWOP": "SEZWOP", "EXPWOP": "EXPWOP"}[o["cat"]],
            taxable_value=o["txval"], igst=o["iamt"], cgst=o["camt"], sgst=o["samt"], cess=0.0,
            invoice_value=o["val"], irp="NIC-IRP1", status="ACT",
            cancelled="N", cancel_date="", qr_signed="Y"))
    # one cancelled IRN with the invoice still live in GSTR-1
    if irn_rows:
        irn_rows[40]["cancelled"] = "Y"
        irn_rows[40]["status"] = "CNL"
        irn_rows[40]["cancel_date"] = ddmmyyyy(dt.datetime.strptime(irn_rows[40]["ack_date"][:10], "%d-%m-%Y").date() + dt.timedelta(days=1))
    write_csv(f"{base}/B4.01_IRN_Register/irn_register_FY2024-25.csv", irn_rows)
    write_json(f"{base}/B4.01_IRN_Register/irn_sample_payload.json", dict(
        Version="1.1", TranDtls=dict(TaxSch="GST", SupTyp="B2B", RegRev="N", IgstOnIntra="N"),
        DocDtls=dict(Typ="INV", No=einv_eligible[0]["inum"], Dt=ddmmyyyy(einv_eligible[0]["idt"])),
        SellerDtls=dict(Gstin=GSTIN_KA, LglNm=LEGAL_NAME, Addr1="Plot 44, Phase II, Peenya",
                        Loc="Bengaluru", Pin=560058, Stcd="29"),
        BuyerDtls=dict(Gstin=einv_eligible[0]["ctin"], LglNm=einv_eligible[0]["cname"],
                       Pos=einv_eligible[0]["pos"], Addr1="Industrial Suburb", Loc="Bengaluru",
                       Pin=560022, Stcd=einv_eligible[0]["pos"]),
        ItemList=[dict(SlNo="1", PrdDesc=einv_eligible[0]["desc"], IsServc="N",
                       HsnCd=einv_eligible[0]["hsn"], Qty=einv_eligible[0]["qty"],
                       Unit=einv_eligible[0]["uqc"], TotAmt=einv_eligible[0]["txval"],
                       AssAmt=einv_eligible[0]["txval"], GstRt=einv_eligible[0]["rate"],
                       IgstAmt=einv_eligible[0]["iamt"], CgstAmt=einv_eligible[0]["camt"],
                       SgstAmt=einv_eligible[0]["samt"], TotItemVal=einv_eligible[0]["val"])],
        ValDtls=dict(AssVal=einv_eligible[0]["txval"], IgstVal=einv_eligible[0]["iamt"],
                     CgstVal=einv_eligible[0]["camt"], SgstVal=einv_eligible[0]["samt"],
                     TotInvVal=einv_eligible[0]["val"]),
        AckNo=irn_rows[0]["ack_no"], AckDt=irn_rows[0]["ack_date"], Irn=irn_rows[0]["irn"]))

    for o in goods:
        if o["inum"] in skip_ewb:
            continue
        gen = dt.datetime.combine(o["idt"], dt.time(R.randint(8, 20), R.randint(0, 59)))
        dist = R.randint(20, 1800)
        ewb_rows.append(dict(
            gstin=o["gstin"], ewb_no=f"{R.randint(101,991)}{R.randint(1000000000, 9999999999)}",
            ewb_date=gen.strftime("%d-%m-%Y %H:%M"), doc_type="INV", doc_no=o["inum"],
            doc_date=ddmmyyyy(o["idt"]), supply_type="Outward",
            sub_supply_type="Export" if o["cat"] == "EXPWOP" else "Supply",
            from_gstin=o["gstin"], from_state=ENTITY_GSTINS[o["gstin"]]["state"],
            from_place="Bengaluru" if o["gstin"] == GSTIN_KA else "Navi Mumbai",
            to_gstin=o["ctin"] or "URP", to_state=o["pos"],
            hsn=o["hsn"], taxable_value=o["txval"], igst=o["iamt"], cgst=o["camt"],
            sgst=o["samt"], cess=0.0, total_invoice_value=o["val"],
            transporter_id=mk_gstin("29", "AAECM1177X"), transporter_name="MAHALAKSHMI ROADLINES PRIVATE LIMITED",
            vehicle_no=f"KA{R.randint(1,53):02d}{R.choice('ABCDEFGHJKL')}{R.choice('ABCDEFGHJKL')}{R.randint(1000,9999)}",
            distance_km=dist, valid_upto=(gen + dt.timedelta(days=max(1, dist // 200 + 1))).strftime("%d-%m-%Y %H:%M"),
            status="Active", cancelled="N", extended="N"))
    if ewb_rows:
        ewb_rows[57]["cancelled"] = "Y"
        ewb_rows[57]["status"] = "Cancelled"
        ewb_rows[102]["extended"] = "Y"
    write_csv(f"{base}/B4.02_EWayBill_Outward_Register/eway_bill_outward_register_FY2024-25.csv", ewb_rows)

    anomaly("B4.01-NO-IRN", ["B4.01", "B1.01", "B4.04"], "FY2024-25",
            "Three B2B/export invoices above the e-invoice threshold have no IRN: "
            + ", ".join(sorted(skip_irn)),
            "Invoice without IRN is legally defective (Rule 48(5)) once taxpayer crosses threshold; "
            "buyer ITC at risk.")
    anomaly("B4.02-NO-EWB", ["B4.02", "B4.03"], "FY2024-25",
            "Two goods invoices above Rs.50,000 have no e-way bill: " + ", ".join(sorted(skip_ewb)),
            "Three-way match invoice vs EWB vs GSTR-1; movement without EWB attracts Section 129.")

    ewb_thr = [
        dict(effective_from="01-02-2018", scope="Inter-state (trial - withdrawn)", state="All India",
             threshold_inr=50000, notification="Notification 74/2017-CT",
             note="Trial rollout suspended on 01-02-2018 due to portal failure"),
        dict(effective_from="01-04-2018", scope="Inter-state", state="All India", threshold_inr=50000,
             notification="Notification 15/2018-CT", note="Mandatory inter-state EWB"),
        dict(effective_from="01-04-2018", scope="Intra-state", state="Karnataka", threshold_inr=50000,
             notification="Karnataka Commercial Taxes notification", note="First state to go live intra-state"),
        dict(effective_from="15-04-2018", scope="Intra-state",
             state="Andhra Pradesh, Gujarat, Kerala, Telangana, Uttar Pradesh", threshold_inr=50000,
             notification="Respective state notifications", note="Phase 2 rollout"),
        dict(effective_from="20-04-2018", scope="Intra-state",
             state="Bihar, Jharkhand, Haryana, Himachal Pradesh, Madhya Pradesh, Tripura, Uttarakhand",
             threshold_inr=50000, notification="Respective state notifications", note="Phase 3 rollout"),
        dict(effective_from="25-04-2018", scope="Intra-state", state="Arunachal Pradesh, Meghalaya, Sikkim, Puducherry",
             threshold_inr=50000, notification="Respective state notifications", note="Phase 4 rollout"),
        dict(effective_from="01-06-2018", scope="Intra-state", state="Maharashtra, Manipur",
             threshold_inr=100000, notification="Maharashtra Notification 15E/2018",
             note="Maharashtra intra-state threshold is Rs.1,00,000, not Rs.50,000"),
        dict(effective_from="03-06-2018", scope="Intra-state", state="All remaining states and UTs",
             threshold_inr=50000, notification="Respective state notifications",
             note="All-India intra-state coverage complete by 16-06-2018"),
        dict(effective_from="02-06-2018", scope="Intra-state", state="Delhi", threshold_inr=100000,
             notification="Delhi Notification 3/2018", note="Delhi intra-state threshold Rs.1,00,000"),
        dict(effective_from="01-06-2018", scope="Intra-state", state="Tamil Nadu", threshold_inr=100000,
             notification="TN Notification 09/2018", note="Tamil Nadu intra-state threshold Rs.1,00,000"),
        dict(effective_from="01-06-2018", scope="Intra-state", state="West Bengal", threshold_inr=100000,
             notification="WB Trade Circular 11/2018",
             note="West Bengal intra-state threshold Rs.1,00,000 (later Rs.50,000 for job work movement)"),
        dict(effective_from="01-04-2019", scope="Intra-state", state="Rajasthan", threshold_inr=100000,
             notification="Rajasthan Notification F.17(131)ACCT/GST/2017/3743",
             note="Rajasthan intra-state threshold revised to Rs.1,00,000"),
    ]
    write_csv(f"{base}/B4.03_EWayBill_Threshold_History/eway_bill_threshold_history.csv", ewb_thr)

    einv_thr = [
        dict(event=1, effective_from="01-10-2020", aato_threshold_inr_crore=500,
             notification="Notification 61/2020-CT and 70/2020-CT",
             aato_reference_period="Any FY from 2017-18 onwards", note="First mandate"),
        dict(event=2, effective_from="01-01-2021", aato_threshold_inr_crore=100,
             notification="Notification 88/2020-CT", aato_reference_period="Any FY from 2017-18 onwards", note=""),
        dict(event=3, effective_from="01-04-2021", aato_threshold_inr_crore=50,
             notification="Notification 5/2021-CT", aato_reference_period="Any FY from 2017-18 onwards", note=""),
        dict(event=4, effective_from="01-04-2022", aato_threshold_inr_crore=20,
             notification="Notification 1/2022-CT", aato_reference_period="Any FY from 2017-18 onwards", note=""),
        dict(event=5, effective_from="01-10-2022", aato_threshold_inr_crore=10,
             notification="Notification 17/2022-CT", aato_reference_period="Any FY from 2017-18 onwards", note=""),
        dict(event=6, effective_from="01-08-2023", aato_threshold_inr_crore=5,
             notification="Notification 10/2023-CT", aato_reference_period="Any FY from 2017-18 onwards",
             note="Current threshold. Exempt classes (SEZ units, banks, insurers, GTA, passenger "
                  "transport, cinema) continue to be excluded."),
    ]
    write_csv(f"{base}/B4.04_EInvoice_Threshold_History/einvoice_threshold_history.csv", einv_thr)


# ================================================= B5 ICEGATE / customs data
def b5_icegate():
    base = "B5_ICEGATE_Customs"
    sb, egm = [], []
    for o in [x for x in UNI.out if x["cat"] == "EXPWOP"]:
        sb.append(dict(shipping_bill_no=o["sbnum"], shipping_bill_date=o["sbdt"],
                       port_code=o["port"], exporter_gstin=o["gstin"], iec="0788012345",
                       branch_sl_no="01", invoice_no=o["inum"], invoice_date=ddmmyyyy(o["idt"]),
                       buyer_name=o["cname"], destination_country=o["country"],
                       hsn=o["hsn"], description=o["desc"],
                       fob_value_inr=o["fob"], currency=o["currency"],
                       fob_value_fc=o["fob_fc"], exchange_rate=o["fx_rate"],
                       export_type="LUT - without payment of IGST", igst_paid=0.0,
                       lut_arn="AD2904240012345K", scheme_code="00 - Free shipping bill",
                       gstr1_table6a_value=o["txval"],
                       mock_flag=o.get("mock_flag", "")))
        egm.append(dict(shipping_bill_no=o["sbnum"], port_code=o["port"],
                        egm_status=o["egm"], egm_date=o["egm_dt"],
                        egm_no=f"EGM{R.randint(100000,999999)}" if o["egm"] == "Filed" else "",
                        vessel_or_flight=f"MV {R.choice(['ORIENT STAR','APL PEARL','MAERSK SEVILLE'])}",
                        leo_date=o["sbdt"],
                        gstn_transmission_status="Transmitted to GSTN" if o["egm"] == "Filed"
                        else "Not transmitted - EGM error",
                        refund_eligibility="Eligible" if o["egm"] == "Filed" else "Blocked"))
    write_csv(f"{base}/B5.01_Shipping_Bill_Data/icegate_shipping_bills_FY2024-25.csv", sb)
    write_csv(f"{base}/B5.02_EGM_Status/icegate_egm_status_FY2024-25.csv", egm)

    boe = []
    for i in UNI.imports:
        boe.append(dict(be_no=i["boe_no"], be_date=ddmmyyyy(i["boe_dt"]), port_code=i["port"],
                        importer_gstin=i["rec_gstin"], iec="0788012345",
                        supplier_name=i["supplier"], origin_country=i["country"],
                        hsn=i["hsn"], assessable_value=i["assessable_value"],
                        bcd=i["bcd"], social_welfare_surcharge=r2(i["bcd"] * 0.1),
                        igst_rate=18, igst_paid=i["igst"], cess=i["cess"],
                        total_duty=r2(i["bcd"] + i["bcd"] * 0.1 + i["igst"]),
                        be_type="Home Consumption", out_of_charge_date=ddmmyyyy(i["boe_dt"] + dt.timedelta(days=2)),
                        gstr2b_reflected="Yes", tax_period_in_2b=fp(i["period"])))
    # defect: one BoE where IGST paid at customs is not reflected in 2B
    if boe:
        boe[3]["gstr2b_reflected"] = "No"
        boe[3]["tax_period_in_2b"] = ""
        boe[3]["mock_flag"] = "ICEGATE-GSTN transmission failure - import IGST credit not in GSTR-2B"
    write_csv(f"{base}/B5.03_Bill_of_Entry_Data/icegate_bill_of_entry_FY2024-25.csv", boe,
              header=list(boe[0].keys()) + ["mock_flag"] if "mock_flag" not in boe[0] else None)
    anomaly("B5.03-IMPG-GAP", ["B5.03", "B1.03", "B1.05"], "FY2024-25",
            f"Bill of Entry {boe[3]['be_no']} carries IGST paid at customs but is not reflected in GSTR-2B (Table IMPG).",
            "Import IGST credit reconciliation: ICEGATE vs GSTR-2B IMPG vs 3B Table 4A(1).")


# ================================================================ B6 DGFT
def b6_dgft():
    base = "B6_DGFT_Portal"
    ebrc = []
    for o in [x for x in UNI.out if x["cat"] == "EXPWOP"]:
        if not o["realised"]:
            ebrc.append(dict(ebrc_no="", ebrc_date="", shipping_bill_no=o["sbnum"],
                             shipping_bill_date=o["sbdt"], invoice_no=o["inum"],
                             invoice_date=ddmmyyyy(o["idt"]), iec="0788012345",
                             ad_code="0510005", bank_name="",
                             currency=o["currency"], invoice_value_fc=o["fob_fc"],
                             realised_value_fc=0.0, realised_value_inr=0.0,
                             realisation_date="", days_from_invoice=(dt.date(2025, 3, 31) - o["idt"]).days,
                             realisation_status="Unrealised",
                             mock_flag="No realisation - Rule 96A 12-month deadline breach"))
            continue
        rdt = o["idt"] + dt.timedelta(days=R.randint(35, 150))
        ebrc.append(dict(ebrc_no=f"EBRC{R.randint(10**9, 10**10-1)}", ebrc_date=ddmmyyyy(rdt),
                         shipping_bill_no=o["sbnum"], shipping_bill_date=o["sbdt"],
                         invoice_no=o["inum"], invoice_date=ddmmyyyy(o["idt"]),
                         iec="0788012345", ad_code="0510005",
                         bank_name="HDFC BANK LTD - PEENYA BRANCH", currency=o["currency"],
                         invoice_value_fc=o["fob_fc"],
                         realised_value_fc=r2(o["fob_fc"] * R.choice([1.0, 1.0, 0.985])),
                         realised_value_inr=r2(o["fob_fc"] * o["fx_rate"]),
                         realisation_date=ddmmyyyy(rdt),
                         days_from_invoice=(rdt - o["idt"]).days,
                         realisation_status="Fully realised", mock_flag=""))
    write_csv(f"{base}/B6.01_eBRC/dgft_ebrc_register_FY2024-25.csv", ebrc)

    write_json(f"{base}/B6.02_IEC_Registry/dgft_iec_registry.json", dict(
        iec="0788012345", iec_issue_date="14-03-2011", entity_name=LEGAL_NAME, pan=PAN,
        status="Active", last_updated="28-06-2024",
        registered_address="Plot 44, Phase II, Peenya Industrial Area, Bengaluru 560058",
        rcmc=[dict(council="EEPC India", rcmc_no="EEPC/RCMC/2023/11842",
                   valid_from="01-04-2023", valid_upto="31-03-2028")],
        branches=[dict(branch_code="01", address="Plot 44, Phase II, Peenya, Bengaluru",
                       linked_gstin=GSTIN_KA),
                  dict(branch_code="02", address="Unit 7, Rabale MIDC, Navi Mumbai",
                       linked_gstin=GSTIN_MH),
                  dict(branch_code="03", address="No 21, Ambattur Industrial Estate, Chennai",
                       linked_gstin="")],
        mock_note=("Branch 03 (Chennai) is NOT linked to the Tamil Nadu GSTIN in the IEC registry - "
                   "IEC-GSTIN linkage failure blocks ICEGATE-GSTN data flow for exports from that unit.")))
    anomaly("B6.02-IEC-LINK", ["B6.02", "A5.02", "B5.01"], "FY2024-25",
            "IEC branch 03 (Chennai) has no GSTIN mapped in the DGFT registry.",
            "IEC-GSTIN linkage validation; mismatch blocks refund processing and SB-GSTR1 matching.")

    eo = [dict(licence_type="EPCG", licence_no="0730012345", licence_date="22-05-2021",
               issuing_authority="DGFT RA Bengaluru", capital_goods_description="CNC machining centre",
               duty_saved_inr=8420000.0, export_obligation_inr=50520000.0,
               eo_period_years=6, eo_due_date="21-05-2027",
               eo_fulfilled_inr=31860000.0, eo_fulfilled_percent=63.1,
               block_wise_status="Block 1 (4 yr, 50%) fulfilled", eodc_status="Not issued",
               status="Live - on track"),
         dict(licence_type="Advance Authorisation", licence_no="0710098765", licence_date="09-08-2022",
              issuing_authority="DGFT RA Bengaluru",
              capital_goods_description="Duty free import of alloy steel bars (input)",
              duty_saved_inr=2140000.0, export_obligation_inr=12840000.0,
              eo_period_years=1.5, eo_due_date="08-02-2024",
              eo_fulfilled_inr=9630000.0, eo_fulfilled_percent=75.0,
              block_wise_status="Not applicable", eodc_status="Not issued",
              status="DEFAULTED - export obligation period expired with 25% shortfall"),
         dict(licence_type="EPCG", licence_no="0730077889", licence_date="30-11-2023",
              issuing_authority="DGFT RA Bengaluru", capital_goods_description="CMM inspection system",
              duty_saved_inr=3180000.0, export_obligation_inr=19080000.0,
              eo_period_years=6, eo_due_date="29-11-2029",
              eo_fulfilled_inr=4290000.0, eo_fulfilled_percent=22.5,
              block_wise_status="Block 1 in progress", eodc_status="Not issued", status="Live")]
    write_csv(f"{base}/B6.03_Export_Obligation_Status/dgft_export_obligation_status.csv", eo)
    anomaly("B6.03-EO-DEFAULT", ["B6.03", "A5.12", "A5.13"], "FY2024-25",
            "Advance Authorisation 0710098765 export obligation period expired 08-02-2024 with a 25% shortfall.",
            "Defaulted EO retroactively affects deemed export / duty-free entitlement claims "
            "(Notification 48/2017); customs duty + IGST + interest exposure.")


def run():
    b2_registration(); b3_notices(); b4_einvoice_ewb(); b5_icegate(); b6_dgft()
