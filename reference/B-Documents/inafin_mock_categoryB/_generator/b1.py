"""B1 - GSTN Filed Returns and Statements."""
import datetime as dt, random
from collections import defaultdict
from core import *

R = random.Random(7)
BASE = "B1_GSTN_Filed_Returns"
FILINGS = []          # for B1.02 ARN register


def arn(gstin, form, period, filed_dt, due_dt, mode="ONLINE"):
    a = f"AA{gstin[:2]}{filed_dt.strftime('%m%y')}{R.randint(1000000,9999999)}{R.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
    late = max(0, (filed_dt - due_dt).days)
    FILINGS.append(dict(gstin=gstin, return_form=form, financial_year=FY, tax_period=fp(period),
                        arn=a, filing_date=ddmmyyyy(filed_dt), due_date=ddmmyyyy(due_dt),
                        filing_status="Filed", delay_days=late, filing_mode=mode,
                        late_fee_paid=r2(min(late * 50, 5000)) if late else 0.0))
    return a


def due(period, form):
    nxt = dt.date(period.year + (period.month == 12), 1 if period.month == 12 else period.month + 1, 1)
    return nxt.replace(day=11) if form == "GSTR1" else nxt.replace(day=20)


# --------------------------------------------------------------- B1.01 GSTR-1
def gstr1():
    rows_flat = []
    for gstin in (GSTIN_KA, GSTIN_MH, GSTIN_TN):
        for p in PERIODS:
            outs = [o for o in UNI.out if o["gstin"] == gstin and o["idt"].month == p.month
                    and o["idt"].year == p.year]
            if not outs:
                continue
            cdns = [c for c in UNI.cdn if c["gstin"] == gstin and c["nt_dt"].month == p.month
                    and c["nt_dt"].year == p.year]
            ads = [a for a in UNI.advances if a["gstin"] == gstin and a["period"] == p]

            b2b = defaultdict(list)
            for o in [x for x in outs if x["cat"] in ("B2B", "SEZWOP")]:
                b2b[o["ctin"]].append(o)
            b2b_blk = [dict(ctin=k, inv=[dict(
                inum=o["inum"], idt=ddmmyyyy(o["idt"]), val=o["val"], pos=o["pos"], rchrg=o["rchrg"],
                inv_typ=o["inv_typ"], itms=[dict(num=1, itm_det=dict(
                    rt=o["rate"], txval=o["txval"], iamt=o["iamt"], camt=o["camt"],
                    samt=o["samt"], csamt=o["csamt"]))]) for o in v]) for k, v in b2b.items()]

            b2cl_pos = defaultdict(list)
            for o in [x for x in outs if x["cat"] == "B2CL"]:
                b2cl_pos[o["pos"]].append(o)
            b2cl_blk = [dict(pos=k, inv=[dict(inum=o["inum"], idt=ddmmyyyy(o["idt"]), val=o["val"],
                        itms=[dict(num=1, itm_det=dict(rt=o["rate"], txval=o["txval"],
                                                       iamt=o["iamt"], csamt=0))]) for o in v])
                        for k, v in b2cl_pos.items()]

            b2cs_blk = [dict(sply_ty="INTRA" if o["pos"] == ENTITY_GSTINS[gstin]["state"] else "INTER",
                             typ="OE", pos=o["pos"], rt=o["rate"], txval=o["txval"],
                             iamt=o["iamt"], camt=o["camt"], samt=o["samt"], csamt=0)
                        for o in outs if o["cat"] == "B2CS"]

            exp = [o for o in outs if o["cat"] == "EXPWOP"]
            exp_blk = [dict(exp_typ="WOPAY", inv=[dict(
                inum=o["inum"], idt=ddmmyyyy(o["idt"]), val=o["txval"],
                sbpcode="INBLR4", sbnum=o.get("sbnum", ""), sbdt=o.get("sbdt", ""),
                itms=[dict(txval=o["txval"], rt=o["rate"], iamt=0, csamt=0)]) for o in exp])] if exp else []

            cdnr_blk = []
            cd_by = defaultdict(list)
            for c in cdns:
                cd_by[c["ctin"]].append(c)
            for k, v in cd_by.items():
                cdnr_blk.append(dict(ctin=k, nt=[dict(
                    ntty=c["ntty"], nt_num=c["nt_num"], nt_dt=ddmmyyyy(c["nt_dt"]),
                    inum=c["inum"], idt=ddmmyyyy(c["idt"]), val=c["val"], pos=c["pos"],
                    rchrg="N", inv_typ="R",
                    itms=[dict(num=1, itm_det=dict(rt=c["rate"], txval=c["txval"], iamt=c["iamt"],
                                                   camt=c["camt"], samt=c["samt"], csamt=0))]) for c in v]))

            at_blk = [dict(pos=a["pos"], sply_ty="INTRA" if a["pos"] == ENTITY_GSTINS[gstin]["state"] else "INTER",
                           itms=[dict(rt=a["rate"], ad_amt=a["ad_amt"], iamt=a["iamt"],
                                      camt=a["camt"], samt=a["samt"], csamt=0)]) for a in ads]

            hsn = defaultdict(lambda: dict(txval=0, iamt=0, camt=0, samt=0, qty=0))
            for o in outs:
                h = hsn[(o["hsn"], o["rate"], o["uqc"])]
                h["txval"] += o["txval"]; h["iamt"] += o["iamt"]
                h["camt"] += o["camt"]; h["samt"] += o["samt"]; h["qty"] += o["qty"]
            hsn_blk = [dict(num=i + 1, hsn_sc=k[0], uqc=k[2], qty=v["qty"], rt=k[1],
                            txval=r2(v["txval"]), iamt=r2(v["iamt"]), camt=r2(v["camt"]),
                            samt=r2(v["samt"]), csamt=0)
                       for i, (k, v) in enumerate(sorted(hsn.items()))]

            filed = due(p, "GSTR1")
            if p == dt.date(2024, 7, 1) and gstin == GSTIN_KA:
                filed = filed + dt.timedelta(days=17)      # late filing anomaly
            a = arn(gstin, "GSTR1", p, filed, due(p, "GSTR1"))

            doc = dict(gstin=gstin, fp=fp(p), gt=None, cur_gt=None, filing_status="Filed",
                       arn=a, filing_date=ddmmyyyy(filed),
                       b2b=b2b_blk, b2cl=b2cl_blk, b2cs=b2cs_blk, cdnr=cdnr_blk,
                       exp=exp_blk, at=at_blk,
                       nil=dict(inv=[dict(sply_ty="INTRB2B", expt_amt=0, nil_amt=0, ngsup_amt=0)]),
                       hsn=dict(data=hsn_blk),
                       doc_issue=dict(doc_det=[dict(doc_num=1, docs=[dict(
                           num=1, from_num=str(min(o["inum"] for o in outs if o["inum"])),
                           to_num=str(max(o["inum"] for o in outs if o["inum"])),
                           totnum=len([o for o in outs if o["inum"]]), cancel=0,
                           net_issue=len([o for o in outs if o["inum"]]))])]))
            write_json(f"{BASE}/B1.01_GSTR-1/GSTR1_{gstin}_{fp(p)}.json", doc)

            for o in outs:
                rows_flat.append(dict(gstin=gstin, tax_period=fp(p), table=o["cat"],
                                      invoice_no=o["inum"] or "", invoice_date=ddmmyyyy(o["idt"]),
                                      ctin=o["ctin"] or "", customer_name=o["cname"],
                                      pos=o["pos"], inv_typ=o["inv_typ"], hsn=o["hsn"],
                                      rate=o["rate"], taxable_value=o["txval"], igst=o["iamt"],
                                      cgst=o["camt"], sgst=o["samt"], cess=0.0, invoice_value=o["val"]))
    write_csv(f"{BASE}/B1.01_GSTR-1/GSTR1_all_lines_FY2024-25.csv", rows_flat)


# ------------------------------------------------- monthly outward liability
def outward_totals(gstin, p):
    t = dict(txval=0, iamt=0, camt=0, samt=0, zero=0)
    for o in UNI.out:
        if o["gstin"] == gstin and (o["idt"].month, o["idt"].year) == (p.month, p.year):
            if o["cat"] in ("EXPWOP",) or o["inv_typ"] == "SEWOP":
                t["zero"] += o["txval"]
            else:
                t["txval"] += o["txval"]; t["iamt"] += o["iamt"]
                t["camt"] += o["camt"]; t["samt"] += o["samt"]
    for c in UNI.cdn:
        if c["gstin"] == gstin and (c["nt_dt"].month, c["nt_dt"].year) == (p.month, p.year):
            t["txval"] -= c["txval"]; t["iamt"] -= c["iamt"]
            t["camt"] -= c["camt"]; t["samt"] -= c["samt"]
    return {k: r2(v) for k, v in t.items()}


def itc_2b(gstin, p):
    """Eligible ITC as per 2B for the period."""
    t = dict(iamt=0, camt=0, samt=0, inelig_i=0, inelig_c=0, inelig_s=0)
    for x in UNI.inward:
        if x["rec_gstin"] == gstin and x["period"] == p:
            if x["itc_avl"] == "Y":
                t["iamt"] += x["iamt"]; t["camt"] += x["camt"]; t["samt"] += x["samt"]
            else:
                t["inelig_i"] += x["iamt"]; t["inelig_c"] += x["camt"]; t["inelig_s"] += x["samt"]
    return {k: r2(v) for k, v in t.items()}


def rcm_totals(gstin, p):
    t = dict(txval=0, iamt=0, camt=0, samt=0)
    for x in UNI.rcm:
        if x["rec_gstin"] == gstin and x["period"] == p:
            t["txval"] += x["txval"]; t["iamt"] += x["iamt"]
            t["camt"] += x["camt"]; t["samt"] += x["samt"]
    return {k: r2(v) for k, v in t.items()}


def import_igst(gstin, p):
    return r2(sum(i["igst"] for i in UNI.imports if i["rec_gstin"] == gstin and i["period"] == p))


# --------------------------------------------------------------- B1.03 GSTR-2B
def gstr2b():
    flat = []
    for gstin in (GSTIN_KA, GSTIN_MH):
        for p in PERIODS:
            invs = [x for x in UNI.inward if x["rec_gstin"] == gstin and x["period"] == p]
            by = defaultdict(list)
            for x in invs:
                by[x["ctin"]].append(x)
            b2b = []
            for ctin, v in by.items():
                b2b.append(dict(ctin=ctin, trdnm=v[0]["sname"], supprd=fp(p),
                                supfileddt=ddmmyyyy(month_end(p) + dt.timedelta(days=11)),
                                supfilingmode="ONLINE",
                                inv=[dict(inum=x["inum"], typ="R", idt=ddmmyyyy(x["idt"]),
                                          val=x["val"], pos=ENTITY_GSTINS[gstin]["state"], rev="N",
                                          itcavl=x["itc_avl"], rsn=x["rsn"], diffprcnt=1,
                                          irn="", irngendate="",
                                          items=[dict(num=1, rt=x["rate"], txval=x["txval"],
                                                      igst=x["iamt"], cgst=x["camt"],
                                                      sgst=x["samt"], cess=0)]) for x in v]))
            imp = [dict(refdt=ddmmyyyy(i["boe_dt"]), portcode=i["port"], boenum=i["boe_no"],
                        boedt=ddmmyyyy(i["boe_dt"]), isamd="N", txval=i["assessable_value"],
                        igst=i["igst"], cess=i["cess"])
                   for i in UNI.imports if i["rec_gstin"] == gstin and i["period"] == p]
            isd = []
            if gstin in (GSTIN_KA, GSTIN_MH):
                share = 0.6 if gstin == GSTIN_KA else 0.4
                amt = r2(180000 * share)
                isd = [dict(ctin=GSTIN_ISD, trdnm=LEGAL_NAME, doctype="ISD",
                            docnum=f"ISD/24-25/{fp(p)}", docdt=ddmmyyyy(month_end(p)),
                            igst=amt if gstin == GSTIN_MH else 0.0,
                            cgst=0.0 if gstin == GSTIN_MH else r2(amt / 2),
                            sgst=0.0 if gstin == GSTIN_MH else r2(amt / 2), cess=0.0)]
            s = itc_2b(gstin, p)
            doc = dict(data=dict(
                gstin=gstin, rtnprd=fp(p), version="2.0",
                gendt=ddmmyyyy(month_end(p) + dt.timedelta(days=14)),
                docdata=dict(b2b=b2b, cdnr=[], impg=imp, isd=isd),
                itcsumm=dict(itcavl=dict(
                    b2b=dict(igst=s["iamt"], cgst=s["camt"], sgst=s["samt"], cess=0),
                    impg=dict(igst=r2(sum(i["igst"] for i in imp)), cgst=0, sgst=0, cess=0),
                    isd=dict(igst=r2(sum(i["igst"] for i in isd)), cgst=r2(sum(i["cgst"] for i in isd)),
                             sgst=r2(sum(i["sgst"] for i in isd)), cess=0)),
                    itcnotavl=dict(b2b=dict(igst=s["inelig_i"], cgst=s["inelig_c"],
                                            sgst=s["inelig_s"], cess=0)))))
            write_json(f"{BASE}/B1.03_GSTR-2B/GSTR2B_{gstin}_{fp(p)}.json", doc)
            for x in invs:
                flat.append(dict(recipient_gstin=gstin, tax_period=fp(p), supplier_gstin=x["ctin"],
                                 supplier_name=x["sname"], invoice_no=x["inum"],
                                 invoice_date=ddmmyyyy(x["idt"]), hsn=x["hsn"], rate=x["rate"],
                                 taxable_value=x["txval"], igst=x["iamt"], cgst=x["camt"],
                                 sgst=x["samt"], cess=0.0, invoice_value=x["val"],
                                 itc_available=x["itc_avl"], itc_unavailable_reason=x["rsn"],
                                 supplier_status_flag="CANCELLED_SUPPLIER" if x.get("flag_cancelled_supplier") else ""))
    write_csv(f"{BASE}/B1.03_GSTR-2B/GSTR2B_all_lines_FY2024-25.csv", flat)


# -------------------------------------------------- B1.04 2B amendment history
def gstr2b_amendments():
    rows = []
    for x in UNI.inward:
        if x.get("amended"):
            d_tx = r2(x["txval"] - x["amend_txval"])
            rows.append(dict(recipient_gstin=x["rec_gstin"], supplier_gstin=x["ctin"],
                             supplier_name=x["sname"], original_invoice_no=x["inum"],
                             original_invoice_date=ddmmyyyy(x["idt"]),
                             original_tax_period=fp(x["period"]),
                             original_taxable_value=x["txval"],
                             original_igst=x["iamt"], original_cgst=x["camt"], original_sgst=x["samt"],
                             amendment_type="R4A - Amended B2B invoice",
                             amended_in_2b_period=fp(x["amend_period"]),
                             amended_taxable_value=x["amend_txval"],
                             amended_igst=r2(x["iamt"] * 0.72), amended_cgst=r2(x["camt"] * 0.72),
                             amended_sgst=r2(x["samt"] * 0.72),
                             taxable_value_delta=-d_tx,
                             itc_delta=-r2((x["iamt"] + x["camt"] + x["samt"]) * 0.28),
                             supplier_amendment_reason="Post supply discount / rate correction",
                             impact="ITC availed in original period exceeds revised eligible ITC"))
    # a second amendment: supplier corrected an invoice number typo (no value impact)
    ref = [x for x in UNI.inward if x["rec_gstin"] == GSTIN_KA
           and x["period"] == dt.date(2024, 6, 1)][2]
    rows.append(dict(recipient_gstin=ref["rec_gstin"], supplier_gstin=ref["ctin"],
                     supplier_name=ref["sname"], original_invoice_no=ref["inum"],
                     original_invoice_date=ddmmyyyy(ref["idt"]), original_tax_period="062024",
                     original_taxable_value=ref["txval"], original_igst=ref["iamt"],
                     original_cgst=ref["camt"], original_sgst=ref["samt"],
                     amendment_type="R4A - Amended B2B invoice (document number correction)",
                     amended_in_2b_period="082024", amended_taxable_value=ref["txval"],
                     amended_igst=ref["iamt"], amended_cgst=ref["camt"], amended_sgst=ref["samt"],
                     taxable_value_delta=0.0, itc_delta=0.0,
                     supplier_amendment_reason="Invoice number corrected by supplier",
                     impact="No ITC impact; document reference in books must be updated"))
    write_csv(f"{BASE}/B1.04_GSTR-2B_Amendment_History/GSTR2B_amendment_history_FY2024-25.csv", rows)
    write_json(f"{BASE}/B1.04_GSTR-2B_Amendment_History/GSTR2B_amendment_history_FY2024-25.json", rows)


# --------------------------------------------------------------- B1.05 GSTR-3B
THREEB_CACHE = {}


def gstr3b():
    summary = []
    for gstin in (GSTIN_KA, GSTIN_MH, GSTIN_TN):
        for p in PERIODS:
            o = outward_totals(gstin, p)
            if o["txval"] == 0 and o["zero"] == 0:
                continue
            s = itc_2b(gstin, p)
            rc = rcm_totals(gstin, p)
            impg = import_igst(gstin, p)
            isd_i = isd_c = isd_s = 0.0
            if gstin == GSTIN_KA:
                isd_c = isd_s = r2(180000 * .6 / 2)
            elif gstin == GSTIN_MH:
                isd_i = r2(180000 * .4)

            # --- seeded deviations ------------------------------------
            note = []
            osup = dict(txval=o["txval"], iamt=o["iamt"], camt=o["camt"], samt=o["samt"], csamt=0.0)
            if gstin == GSTIN_KA and p == dt.date(2024, 9, 1):
                miss = sorted([x for x in UNI.out if x["gstin"] == gstin and x["cat"] == "B2B"
                               and (x["idt"].month, x["idt"].year) == (9, 2024)],
                              key=lambda z: -z["txval"])[:3]
                osup = dict(txval=r2(o["txval"] - sum(m["txval"] for m in miss)),
                            iamt=r2(o["iamt"] - sum(m["iamt"] for m in miss)),
                            camt=r2(o["camt"] - sum(m["camt"] for m in miss)),
                            samt=r2(o["samt"] - sum(m["samt"] for m in miss)),
                            csamt=0.0)
                note.append("UNDER-DECLARED vs GSTR-1 (see B3.02 DRC-01B)")
            itc_i, itc_c, itc_s = s["iamt"], s["camt"], s["samt"]
            if gstin == GSTIN_KA and p == dt.date(2024, 10, 1):
                itc_i, itc_c, itc_s = r2(itc_i * .8), r2(itc_c * .8), r2(itc_s * .8)
                note.append("ITC availed LESS than GSTR-2B (deferred availment)")
            if gstin == GSTIN_KA and p == dt.date(2025, 1, 1):
                itc_c, itc_s = r2(itc_c * 1.06), r2(itc_s * 1.06)
                note.append("ITC availed EXCESS of GSTR-2B (Sec 16(2)(aa) breach)")

            rev42 = r2((itc_c + itc_s + itc_i) * 0.035)
            doc = dict(
                gstin=gstin, ret_period=fp(p), form="GSTR3B",
                sup_details=dict(
                    osup_det=osup,
                    osup_zero=dict(txval=o["zero"], iamt=0.0, csamt=0.0),
                    osup_nil_exmp=dict(txval=0.0),
                    isup_rev=dict(txval=rc["txval"], iamt=rc["iamt"], camt=rc["camt"],
                                  samt=rc["samt"], csamt=0.0),
                    osup_nongst=dict(txval=0.0)),
                inter_sup=dict(unreg_details=[], comp_details=[], uin_details=[]),
                itc_elg=dict(
                    itc_avl=[
                        dict(ty="IMPG", iamt=impg, camt=0.0, samt=0.0, csamt=0.0),
                        dict(ty="IMPS", iamt=r2(sum(x["iamt"] for x in UNI.rcm
                                                    if x["rec_gstin"] == gstin and x["period"] == p
                                                    and x["kind"] == "IMPS")),
                             camt=0.0, samt=0.0, csamt=0.0),
                        dict(ty="ISRC", iamt=rc["iamt"], camt=rc["camt"], samt=rc["samt"], csamt=0.0),
                        dict(ty="ISD", iamt=isd_i, camt=isd_c, samt=isd_s, csamt=0.0),
                        dict(ty="OTH", iamt=itc_i, camt=itc_c, samt=itc_s, csamt=0.0)],
                    itc_rev=[dict(ty="RUL", iamt=r2(rev42 * .34), camt=r2(rev42 * .33),
                                  samt=r2(rev42 * .33), csamt=0.0),
                             dict(ty="OTH", iamt=0.0, camt=0.0, samt=0.0, csamt=0.0)],
                    itc_net=dict(iamt=r2(impg + rc["iamt"] + isd_i + itc_i - rev42 * .34),
                                 camt=r2(rc["camt"] + isd_c + itc_c - rev42 * .33),
                                 samt=r2(rc["samt"] + isd_s + itc_s - rev42 * .33), csamt=0.0),
                    itc_inelg=[dict(ty="RUL", iamt=s["inelig_i"], camt=s["inelig_c"],
                                    samt=s["inelig_s"], csamt=0.0),
                               dict(ty="OTH", iamt=0.0, camt=0.0, samt=0.0, csamt=0.0)]),
                inward_sup=dict(isup_details=[dict(ty="GST", inter=0.0, intra=0.0),
                                              dict(ty="NONGST", inter=0.0, intra=0.0)]),
                intr_ltfee=dict(intr_details=dict(iamt=0.0, camt=0.0, samt=0.0, csamt=0.0)),
                filing_status="Filed", notes_for_mock=note)
            filed = due(p, "GSTR3B")
            if p == dt.date(2024, 7, 1) and gstin == GSTIN_KA:
                filed = filed + dt.timedelta(days=12)
            doc["arn"] = arn(gstin, "GSTR3B", p, filed, due(p, "GSTR3B"))
            doc["filing_date"] = ddmmyyyy(filed)
            write_json(f"{BASE}/B1.05_GSTR-3B/GSTR3B_{gstin}_{fp(p)}.json", doc)
            THREEB_CACHE[(gstin, fp(p))] = doc
            summary.append(dict(gstin=gstin, tax_period=fp(p),
                                t31a_taxable_value=osup["txval"], t31a_igst=osup["iamt"],
                                t31a_cgst=osup["camt"], t31a_sgst=osup["samt"],
                                t31b_zero_rated=o["zero"], t31d_inward_rcm=rc["txval"],
                                t4a5_itc_others_igst=itc_i, t4a5_itc_others_cgst=itc_c,
                                t4a5_itc_others_sgst=itc_s, t4a1_import_goods_igst=impg,
                                t4a3_rcm_igst=rc["iamt"], t4a3_rcm_cgst=rc["camt"],
                                t4a3_rcm_sgst=rc["samt"], t4a4_isd_igst=isd_i,
                                t4a4_isd_cgst=isd_c, t4a4_isd_sgst=isd_s,
                                t4b_reversal_total=rev42,
                                gstr1_taxable_value=o["txval"],
                                gstr1_minus_3b_delta=r2(o["txval"] - osup["txval"]),
                                mock_note="; ".join(note)))
    write_csv(f"{BASE}/B1.05_GSTR-3B/GSTR3B_summary_FY2024-25.csv", summary)
    anomaly("B1.05-3B-VS-1", ["B1.01", "B1.05", "B3.02"], "092024",
            "Sep-2024 GSTR-3B Table 3.1(a) understates outward taxable value vs GSTR-1 by one B2B invoice.",
            "GSTR-1 vs GSTR-3B liability reconciliation; matches DRC-01B notice in B3.02.")
    anomaly("B1.05-ITC-SHORT", ["B1.03", "B1.05"], "102024",
            "Oct-2024 ITC availed is 80% of GSTR-2B eligible ITC (deferred availment).",
            "2B vs 3B Table 4A(5) reconciliation; recovery opportunity per Circular 183/15/2022.")
    anomaly("B1.05-ITC-EXCESS", ["B1.03", "B1.05"], "012025",
            "Jan-2025 CGST/SGST ITC availed is 106% of GSTR-2B eligible ITC.",
            "Section 16(2)(aa) cap breach; excess ITC + interest u/s 50(3).")
    anomaly("B1.02-LATE", ["B1.02"], "072024",
            "Jul-2024 GSTR-1 and GSTR-3B for the Karnataka GSTIN filed after the due date.",
            "Late fee and Section 50 interest computation from due date.")


# ------------------------------------------------------------- B1.02 ARN reg.
def arn_register():
    # ISD + TDS/TCS counterpart filings are appended by their own emitters first
    write_csv(f"{BASE}/B1.02_ARN_and_Filing_Date/return_filing_arn_register_FY2024-25.csv",
              sorted(FILINGS, key=lambda x: (x["gstin"], x["return_form"], x["tax_period"][2:], x["tax_period"][:2])))


# --------------------------------------------------------------- B1.06 GSTR-9
def gstr9():
    for gstin in (GSTIN_KA, GSTIN_MH, GSTIN_TN):
        tot = defaultdict(float)
        for p in PERIODS:
            o = outward_totals(gstin, p)
            s = itc_2b(gstin, p)
            rc = rcm_totals(gstin, p)
            k3b = THREEB_CACHE.get((gstin, fp(p)))
            if not k3b:
                continue
            tot["gstr1_txval"] += o["txval"]; tot["gstr1_iamt"] += o["iamt"]
            tot["gstr1_camt"] += o["camt"]; tot["gstr1_samt"] += o["samt"]
            tot["zero"] += o["zero"]
            tot["rcm_txval"] += rc["txval"]; tot["rcm_tax"] += rc["iamt"] + rc["camt"] + rc["samt"]
            tot["3b_txval"] += k3b["sup_details"]["osup_det"]["txval"]
            tot["3b_iamt"] += k3b["sup_details"]["osup_det"]["iamt"]
            tot["3b_camt"] += k3b["sup_details"]["osup_det"]["camt"]
            tot["3b_samt"] += k3b["sup_details"]["osup_det"]["samt"]
            oth = [x for x in k3b["itc_elg"]["itc_avl"] if x["ty"] == "OTH"][0]
            tot["3b_itc_i"] += oth["iamt"]; tot["3b_itc_c"] += oth["camt"]; tot["3b_itc_s"] += oth["samt"]
            tot["2b_i"] += s["iamt"]; tot["2b_c"] += s["camt"]; tot["2b_s"] += s["samt"]
            tot["impg"] += import_igst(gstin, p)
        if not tot:
            continue
        t = {k: r2(v) for k, v in tot.items()}
        doc = dict(
            gstin=gstin, fy=FY, form="GSTR9", filing_status="Filed",
            arn=arn(gstin, "GSTR9", dt.date(2025, 3, 1), dt.date(2025, 12, 18), dt.date(2025, 12, 31)),
            filing_date="18-12-2025",
            table4=dict(
                B_b2b=dict(txval=t["gstr1_txval"], iamt=t["gstr1_iamt"],
                           camt=t["gstr1_camt"], samt=t["gstr1_samt"], csamt=0.0),
                G_inward_rcm=dict(txval=t["rcm_txval"], tax=t["rcm_tax"]),
                N_supplies_on_which_tax_payable=dict(txval=r2(t["gstr1_txval"] + t["rcm_txval"]))),
            table5=dict(A_zero_rated_without_payment=dict(txval=t["zero"]),
                        N_supplies_on_which_tax_not_payable=dict(txval=t["zero"])),
            table6=dict(
                A_itc_as_per_3b=dict(iamt=t["3b_itc_i"], camt=t["3b_itc_c"], samt=t["3b_itc_s"]),
                B_inputs_and_services=dict(iamt=t["3b_itc_i"], camt=t["3b_itc_c"], samt=t["3b_itc_s"]),
                E_import_of_goods=dict(iamt=t["impg"])),
            table8=dict(
                A_itc_as_per_2B=dict(iamt=t["2b_i"], camt=t["2b_c"], samt=t["2b_s"]),
                B_itc_availed_in_3b=dict(iamt=t["3b_itc_i"], camt=t["3b_itc_c"], samt=t["3b_itc_s"]),
                D_difference=dict(iamt=r2(t["2b_i"] - t["3b_itc_i"]),
                                  camt=r2(t["2b_c"] - t["3b_itc_c"]),
                                  samt=r2(t["2b_s"] - t["3b_itc_s"]))),
            table9_tax_paid=dict(igst=t["3b_iamt"], cgst=t["3b_camt"], sgst=t["3b_samt"]),
            table11_supplies_declared_after_march=dict(txval=0.0),
            table14_differential_tax_paid=dict(igst=0.0, cgst=0.0, sgst=0.0),
            mock_note=("Table 4 is built from GSTR-1; Table 9 from GSTR-3B. The Sep-2024 3B "
                       "under-declaration therefore surfaces as a Table 4 vs Table 9 gap."))
        write_json(f"{BASE}/B1.06_GSTR-9_Annual_Return/GSTR9_{gstin}_{FY}.json", doc)


# -------------------------------------------------------------- B1.07 GSTR-9C
def gstr9c():
    gstin = GSTIN_KA
    turn = r2(sum(outward_totals(gstin, p)["txval"] + outward_totals(gstin, p)["zero"] for p in PERIODS))
    other_income = 4820000.0        # interest, dividend, forex gain - non GST
    unbilled_open, unbilled_close = 3150000.0, 4670000.0
    deemed = 1260000.0              # cross charge / schedule I supplies in books but not revenue
    audited = r2(turn + other_income - deemed + (unbilled_close - unbilled_open))
    doc = dict(
        gstin=gstin, fy=FY, form="GSTR9C", filing_status="Filed",
        filing_date="18-12-2025",
        table5_reconciliation_of_turnover=dict(
            A_turnover_as_per_audited_financials=audited,
            B_unbilled_revenue_at_beginning=unbilled_open,
            C_unadjusted_advances_at_end=1890000.0,
            D_deemed_supply_schedule_I=deemed,
            E_credit_notes_after_march_31=0.0,
            F_trade_discounts_not_permissible=0.0,
            G_turnover_from_april_to_june_2017=0.0,
            H_unbilled_revenue_at_end=unbilled_close,
            I_unadjusted_advances_at_beginning=1440000.0,
            J_credit_notes_accounted_but_not_permissible=0.0,
            K_adjustments_on_forex_fluctuation=318500.0,
            L_other_adjustments=0.0,
            O_other_adjustments_to_reconcile=0.0,
            P_annual_turnover_after_adjustments=r2(audited + unbilled_open + 1890000.0 + deemed
                                                   - unbilled_close - 1440000.0 - other_income + 318500.0),
            Q_turnover_as_declared_in_annual_return=turn,
            R_un_reconciled_turnover=None),
        table6_reasons_for_un_reconciled_turnover=[
            dict(reason="Forex revaluation on export invoices booked at bank rate vs CBEC notified rate",
                 amount=318500.0),
            dict(reason="Cross-charge / Schedule I inter-GSTIN supplies recorded in books but not "
                        "declared in GSTR-1 (to be traced by the platform)", amount=None)],
        table12_reconciliation_of_itc=dict(
            A_itc_availed_as_per_audited_financials=None,
            B_itc_booked_in_earlier_fy_claimed_in_current=412000.0,
            C_itc_booked_in_current_fy_to_be_claimed_next=968000.0,
            E_itc_claimed_in_annual_return=None),
        auditor=dict(name="S. RANGANATHAN & ASSOCIATES, Chartered Accountants",
                     membership_no="218764", firm_regn_no="004512S", udin="25218764BKXQRS1234"),
        mock_note=("R (un-reconciled turnover) and Table 12 A/E are deliberately left null - the "
                   "platform is expected to compute the bridge independently and compare."))
    p = doc["table5_reconciliation_of_turnover"]
    doc["table5_reconciliation_of_turnover"]["R_un_reconciled_turnover"] = r2(p["P_annual_turnover_after_adjustments"] - p["Q_turnover_as_declared_in_annual_return"])
    doc["table6_reasons_for_un_reconciled_turnover"][1]["amount"] = r2(
        doc["table5_reconciliation_of_turnover"]["R_un_reconciled_turnover"] - 318500.0)
    write_json(f"{BASE}/B1.07_GSTR-9C/GSTR9C_{gstin}_{FY}.json", doc)


# -------------------------------------------------------- B1.08 GSTR-2A (hist)
def gstr2a():
    """Forensic-mode sample: FY 2018-19 monthly GSTR-2A for the KA GSTIN."""
    rng = random.Random(99)
    for m in range(4, 7):
        p = dt.date(2018, m, 1)
        b2b = []
        for s in SUPPLIERS[:5]:
            invs = []
            for k in range(rng.randint(2, 4)):
                txval = r2(rng.randint(60000, 900000))
                intra = s["state"] == "29"
                tax = r2(txval * 0.18)
                invs.append(dict(inum=f"{s['name'][:3].upper()}/18-19/{rng.randint(100,999)}",
                                 idt=ddmmyyyy(dt.date(2018, m, rng.randint(1, 28))),
                                 val=r2(txval + tax), pos="29", rchrg="N", inv_typ="R",
                                 chksum="", cfs="Y",
                                 itms=[dict(num=1, itm_det=dict(
                                     rt=18, txval=txval,
                                     iamt=0.0 if intra else tax,
                                     camt=r2(tax / 2) if intra else 0.0,
                                     samt=r2(tax / 2) if intra else 0.0, csamt=0.0))]))
            b2b.append(dict(ctin=s["gstin"], cfs="Y", fldtr1=ddmmyyyy(month_end(p) + dt.timedelta(days=10)),
                            fldprd=fp(p), inv=invs))
        write_json(f"{BASE}/B1.08_GSTR-2A_Historical/GSTR2A_{GSTIN_KA}_{fp(p)}.json",
                   dict(gstin=GSTIN_KA, fp=fp(p), b2b=b2b, cdn=[], isd=[], impg=[],
                        mock_note="Pre-GSTR-2B period sample for Forensic Mode (FY 2018-19)."))


# ------------------------------------------------------------ B1.09 GSTR-6/6A
def gstr6():
    rng = random.Random(31)
    for p in PERIODS:
        common = []
        for s in [x for x in SUPPLIERS if x["cat"] == "isd"]:
            txval = r2(rng.randint(90000, 310000))
            tax = r2(txval * 0.18)
            common.append(dict(ctin=s["gstin"], name=s["name"],
                               inum=f"{s['name'][:3].upper()}/24-25/{rng.randint(100,999)}",
                               idt=ddmmyyyy(dt.date(p.year, p.month, rng.randint(1, 25))),
                               txval=txval, camt=r2(tax / 2), samt=r2(tax / 2), iamt=0.0))
        total = r2(sum(c["camt"] + c["samt"] for c in common))
        # turnover based distribution ratio
        ratios = {GSTIN_KA: 0.58, GSTIN_MH: 0.31, GSTIN_TN: 0.11}
        dist = []
        for g, ratio in ratios.items():
            amt = r2(total * ratio)
            intra = ENTITY_GSTINS[g]["state"] == "29"
            dist.append(dict(gstin=g, doc_num=f"ISD/24-25/{fp(p)}/{g[:2]}",
                             doc_dt=ddmmyyyy(month_end(p)), elig="E",
                             igst=0.0 if intra else amt,
                             cgst=r2(amt / 2) if intra else 0.0,
                             sgst=r2(amt / 2) if intra else 0.0, cess=0.0))
        doc = dict(gstin=GSTIN_ISD, ret_period=fp(p), form="GSTR6", filing_status="Filed",
                   arn=arn(GSTIN_ISD, "GSTR6", p, month_end(p) + dt.timedelta(days=13),
                           month_end(p) + dt.timedelta(days=13)),
                   b2b=[dict(ctin=c["ctin"], inv=[dict(inum=c["inum"], idt=c["idt"],
                        val=r2(c["txval"] * 1.18), pos="29", itms=[dict(num=1, itm_det=dict(
                            rt=18, txval=c["txval"], camt=c["camt"], samt=c["samt"],
                            iamt=c["iamt"], csamt=0.0))])]) for c in common],
                   isd=dist,
                   distribution_basis="Turnover of preceding financial year - Rule 39(1)(d)",
                   mock_note=("TN GSTIN was registered only from 12-08-2024 - distributions to it "
                              "before that date are a deliberate defect for pre-Aug periods."))
        write_json(f"{BASE}/B1.09_GSTR-6_ISD/GSTR6_{GSTIN_ISD}_{fp(p)}.json", doc)
    anomaly("B1.09-ISD-PREREG", ["B1.09", "B2.01", "B2.06"], "042024-072024",
            "ISD distributed credit to the Tamil Nadu GSTIN for periods before its registration "
            "effective date (12-08-2024).",
            "ISD distribution must only go to GSTINs registered on the distribution date - Rule 39.")


# ------------------------------------------------------------- B1.10 GSTR-7
def gstr7():
    rows = []
    deductor = CUSTOMERS[7]
    rng = random.Random(53)
    for p in PERIODS:
        invs = [o for o in UNI.out if o["ctin"] == deductor["gstin"]
                and (o["idt"].month, o["idt"].year) == (p.month, p.year)]
        for o in invs:
            tds_rate = 2.0
            intra = o["pos"] == "29"
            tds = r2(o["txval"] * tds_rate / 100)
            rows.append(dict(deductor_gstin=deductor["gstin"], deductor_name=deductor["name"],
                             tax_period=fp(p), deductee_gstin=o["gstin"],
                             deductee_name=LEGAL_NAME, invoice_no=o["inum"],
                             invoice_date=ddmmyyyy(o["idt"]), taxable_value=o["txval"],
                             tds_rate_percent=tds_rate,
                             tds_igst=0.0 if intra else tds,
                             tds_cgst=r2(tds / 2) if intra else 0.0,
                             tds_sgst=r2(tds / 2) if intra else 0.0,
                             total_tds=tds, gstr7_filing_date=ddmmyyyy(month_end(p) + dt.timedelta(days=10)),
                             tds_certificate_no=f"GSTR7A/{fp(p)}/{rng.randint(10000,99999)}"))
    write_csv(f"{BASE}/B1.10_GSTR-7_TDS/GSTR7_TDS_credit_FY2024-25.csv", rows)
    write_json(f"{BASE}/B1.10_GSTR-7_TDS/GSTR7_TDS_credit_FY2024-25.json",
               dict(deductee_gstin=GSTIN_KA, fy=FY, records=rows,
                    total_tds_credit=r2(sum(x["total_tds"] for x in rows)),
                    mock_note="TDS credit accepted in the electronic cash ledger reduces net tax payable."))


# ------------------------------------------------------------- B1.11 GSTR-8
def gstr8():
    """Marketplace operator TCS declared against our GSTIN, state-wise."""
    rng = random.Random(77)
    operators = [dict(name="AMAZON SELLER SERVICES PRIVATE LIMITED", gstin=mk_gstin("29", "AAICA1234E")),
                 dict(name="FLIPKART INTERNET PRIVATE LIMITED", gstin=mk_gstin("29", "AABCF5678M"))]
    rows = []
    for p in PERIODS:
        for op in operators:
            for pos in ["29", "27", "07", "33", "36", "19"]:
                gross = r2(rng.randint(180000, 1400000))
                ret = r2(gross * rng.uniform(0.03, 0.12))
                net = r2(gross - ret)
                tcs = r2(net * 0.005)
                intra = pos == "29"
                rows.append(dict(operator_gstin=op["gstin"], operator_name=op["name"],
                                 tax_period=fp(p), supplier_gstin=GSTIN_KA,
                                 supplier_name=LEGAL_NAME, pos=pos,
                                 gross_value_of_supplies=gross, value_of_supplies_returned=ret,
                                 net_value_of_supplies=net, tcs_rate_percent=0.5,
                                 tcs_igst=0.0 if intra else tcs,
                                 tcs_cgst=r2(tcs / 2) if intra else 0.0,
                                 tcs_sgst=r2(tcs / 2) if intra else 0.0,
                                 total_tcs=tcs,
                                 gstr8_filing_date=ddmmyyyy(month_end(p) + dt.timedelta(days=10))))
    # deliberate defect: operator declared TCS for Dec-24 on a base 9% higher than our books
    for r_ in rows:
        if r_["tax_period"] == "122024" and "AMAZON" in r_["operator_name"] and r_["pos"] == "29":
            r_["gross_value_of_supplies"] = r2(r_["gross_value_of_supplies"] * 1.09)
            r_["net_value_of_supplies"] = r2(r_["net_value_of_supplies"] * 1.09)
            r_["total_tcs"] = r2(r_["net_value_of_supplies"] * 0.005)
            r_["tcs_cgst"] = r_["tcs_sgst"] = r2(r_["total_tcs"] / 2)
            r_["mock_flag"] = "TCS base exceeds seller-declared marketplace turnover by 9%"
    write_csv(f"{BASE}/B1.11_GSTR-8_TCS/GSTR8_TCS_by_operator_FY2024-25.csv", rows,
              header=list(rows[0].keys()) + ["mock_flag"] if "mock_flag" not in rows[0] else None)
    write_json(f"{BASE}/B1.11_GSTR-8_TCS/GSTR8_TCS_by_operator_FY2024-25.json",
               dict(supplier_gstin=GSTIN_KA, fy=FY, records=rows,
                    total_tcs=r2(sum(x["total_tcs"] for x in rows))))
    anomaly("B1.11-TCS-BASE", ["B1.11", "D1.01"], "122024",
            "Amazon GSTR-8 net supply value for POS 29 in Dec-2024 exceeds the seller's own "
            "marketplace turnover by 9%.",
            "GSTR-8 vs MTR vs GSTR-1 three-way marketplace reconciliation (Section A9).")


def run():
    gstr1(); gstr2b(); gstr2b_amendments(); gstr3b()
    gstr9(); gstr9c(); gstr2a(); gstr6(); gstr7(); gstr8()
    arn_register()
