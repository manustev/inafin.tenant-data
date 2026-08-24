import os, json, datetime as dt
from core import *
import b2_b6, b1

# shipping bills must exist before GSTR-1 Table 6A is written
b2_b6.prepare_shipping_bills()
b1.run()
b2_b6.run()

# ------------------------------------------------------------------ PDFs
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)

S = getSampleStyleSheet()
H = ParagraphStyle("h", parent=S["Title"], fontSize=13, spaceAfter=4)
SUB = ParagraphStyle("s", parent=S["Normal"], fontSize=9, alignment=1, textColor=colors.grey)
N = ParagraphStyle("n", parent=S["Normal"], fontSize=8.5, leading=11)
WM = ParagraphStyle("w", parent=S["Normal"], fontSize=7.5, textColor=colors.red, alignment=1)

TBL = TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#888888")),
                  ("FONTSIZE", (0, 0), (-1, -1), 8),
                  ("VALIGN", (0, 0), (-1, -1), "TOP"),
                  ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eeeeee")),
                  ("LEFTPADDING", (0, 0), (-1, -1), 5),
                  ("TOPPADDING", (0, 0), (-1, -1), 3),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])


def kv_table(pairs, w=(58 * mm, 112 * mm)):
    t = Table([[Paragraph(f"<b>{k}</b>", N), Paragraph(str(v), N)] for k, v in pairs], colWidths=w)
    t.setStyle(TBL)
    return t


def build_pdf(path, story):
    p = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    SimpleDocTemplate(p, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm,
                      leftMargin=20 * mm, rightMargin=20 * mm).build(story)


def reg06_pdf():
    m = ENTITY_GSTINS[GSTIN_KA]
    st = [Paragraph("Government of India", SUB),
          Paragraph("Form GST REG-06<br/>[See Rule 10(1)]<br/>Registration Certificate", H),
          Paragraph("MOCK / SYNTHETIC DOCUMENT - generated for INAFIN platform testing. "
                    "Not a genuine GSTN document and carries no legal effect.", WM),
          Spacer(1, 8),
          kv_table([("Registration Number (GSTIN)", GSTIN_KA),
                    ("Legal Name", LEGAL_NAME),
                    ("Trade Name", TRADE_NAME),
                    ("Constitution of Business", "Private Limited Company"),
                    ("Address of Principal Place of Business", m["addr"]),
                    ("Date of Liability", m["eff"]),
                    ("Period of Validity", "From 01-07-2017 &nbsp;&nbsp; To: Not Applicable"),
                    ("Type of Registration", m["rtype"]),
                    ("Particulars of Approving Authority",
                     "Centre - Bengaluru North Commissionerate, Division IV, Range AD"),
                    ("Date of issue of Certificate", "01-07-2017")]),
          Spacer(1, 10),
          Paragraph("<b>Annexure A - Details of Additional Places of Business</b>", N),
          Spacer(1, 4),
          kv_table([("1", "Warehouse 3, Nelamangala Road, Bengaluru 562123 (Warehouse / Depot)")],
                   w=(20 * mm, 150 * mm)),
          Spacer(1, 10),
          Paragraph("<b>Annexure B - Details of Persons in Charge</b>", N),
          Spacer(1, 4)]
    t = Table([["Sl", "Name", "Designation", "Resident of State"],
               ["1", "RAJEEV MENON", "Director", "Karnataka"],
               ["2", "SUNITHA RAO", "Company Secretary", "Karnataka"]],
              colWidths=(14 * mm, 60 * mm, 50 * mm, 46 * mm))
    t.setStyle(TBL)
    st += [t, Spacer(1, 12),
           Paragraph("This is a system generated digitally signed Registration Certificate.", N)]
    build_pdf("B2_Registration_and_Status/B2.01_REG-06_Taxpayer/REG06_" + GSTIN_KA + ".pdf", st)


def drc01b_pdf():
    import csv as _csv
    rows = list(_csv.DictReader(open(os.path.join(
        OUT, "B1_GSTN_Filed_Returns/B1.05_GSTR-3B/GSTR3B_summary_FY2024-25.csv"))))
    r = [x for x in rows if x["gstin"] == GSTIN_KA and x["tax_period"] == "092024"][0]
    g1 = float(r["gstr1_taxable_value"]); tb = float(r["t31a_taxable_value"])
    d_tx = r2(g1 - tb)
    # tax differential at 18%
    d_tax = r2(d_tx * 0.18)
    st = [Paragraph("Form GST DRC-01B (Part A)", H),
          Paragraph("[See Rule 88C] - Intimation of difference in liability reported in statement "
                    "of outward supplies and that reported in return", SUB),
          Paragraph("MOCK / SYNTHETIC DOCUMENT - generated for INAFIN platform testing. "
                    "Not a genuine GSTN document and carries no legal effect.", WM),
          Spacer(1, 8),
          kv_table([("Reference No.", "DRC01B/29/202409/00417"),
                    ("Date", "26-11-2024"),
                    ("GSTIN", GSTIN_KA),
                    ("Legal Name", LEGAL_NAME),
                    ("Tax Period", "September 2024"),
                    ("Financial Year", FY)]),
          Spacer(1, 10),
          Paragraph("It has been noticed that the tax payable in respect of outward supplies "
                    "furnished in FORM GSTR-1 exceeds the tax paid in FORM GSTR-3B for the said "
                    "tax period, as detailed below:", N),
          Spacer(1, 6)]
    t = Table([["Particulars", "Taxable value (Rs.)", "IGST (Rs.)", "CGST (Rs.)", "SGST (Rs.)"],
               ["Liability declared in GSTR-1 / IFF", f"{g1:,.2f}", f"{float(r['t31a_igst']) + 0:,.2f}", "", ""],
               ["Liability paid in GSTR-3B (Table 3.1)", f"{tb:,.2f}", "", "", ""],
               ["Difference", f"{d_tx:,.2f}", "", "", f"tax approx. {d_tax:,.2f}"]],
              colWidths=(58 * mm, 34 * mm, 26 * mm, 26 * mm, 26 * mm))
    t.setStyle(TBL)
    st += [t, Spacer(1, 10),
           Paragraph("You are hereby directed to either pay the differential tax liability along "
                     "with interest under Section 50 through FORM GST DRC-03, or furnish an "
                     "explanation for the difference in FORM GST DRC-01B Part B, within a period of "
                     "seven days. Failure to do so will render you liable to proceedings under "
                     "Section 79 and will block the furnishing of FORM GSTR-1 for the subsequent "
                     "tax period under Rule 59(6).", N),
           Spacer(1, 12),
           kv_table([("Response due date", "26-12-2024"), ("Status", "Open - no response filed"),
                     ("Issued by", "System generated - GSTN")])]
    build_pdf("B3_Notices_Proceedings_Settlements/B3.02_DRC-01B_Notices/DRC01B_29_092024.pdf", st)
    # patch the JSON with the real numbers
    p = os.path.join(OUT, "B3_Notices_Proceedings_Settlements/B3.02_DRC-01B_Notices/drc01b_notices.json")
    j = json.load(open(p))
    j[0]["liability_declared_in_gstr1"] = g1
    j[0]["liability_paid_in_gstr3b"] = tb
    j[0]["difference"] = d_tx
    j[0]["approx_tax_differential_at_18pc"] = d_tax
    json.dump(j, open(p, "w"), indent=2)


def scn_pdf():
    st = [Paragraph("Form GST DRC-01", H),
          Paragraph("[See Rule 142(1)(a)] - Summary of Show Cause Notice", SUB),
          Paragraph("MOCK / SYNTHETIC DOCUMENT - generated for INAFIN platform testing. "
                    "Not a genuine GSTN document and carries no legal effect.", WM),
          Spacer(1, 8),
          kv_table([("Reference No.", "ZD290920240012345"), ("Date", "18-09-2024"),
                    ("GSTIN", GSTIN_KA), ("Legal Name", LEGAL_NAME),
                    ("Tax Period", "April 2021 to March 2022"),
                    ("Section under which notice issued", "Section 74 of the CGST Act, 2017"),
                    ("Issue", "Excess input tax credit availed as compared to the credit "
                              "reflected in FORM GSTR-2A / GSTR-2B - Section 16(2)(aa)"),
                    ("Date of reply", "18-10-2024"),
                    ("Date of personal hearing", "27-11-2024")]),
          Spacer(1, 10)]
    t = Table([["Sl", "Head", "Tax (Rs.)", "Interest (Rs.)", "Penalty (Rs.)", "Total (Rs.)"],
               ["1", "IGST", "48,20,000.00", "19,28,000.00", "48,20,000.00", "1,15,68,000.00"],
               ["", "Total", "48,20,000.00", "19,28,000.00", "48,20,000.00", "1,15,68,000.00"]],
              colWidths=(12 * mm, 26 * mm, 33 * mm, 33 * mm, 33 * mm, 33 * mm))
    t.setStyle(TBL)
    st += [t, Spacer(1, 10),
           Paragraph("You are hereby directed to show cause as to why the amount specified above "
                     "should not be demanded and recovered from you, along with applicable interest "
                     "and penalty. A reply may be filed in FORM GST DRC-06 on the common portal.", N),
           Spacer(1, 8),
           Paragraph("<b>Note for platform testing:</b> a stay order (WP 21874/2024, High Court of "
                     "Karnataka, 04-12-2024) is on record against this demand and a part payment of "
                     "Rs.21,12,000 was made through DRC-03 AD290225000456Y. Reconciliation output "
                     "must not re-raise this issue as an open anomaly.", N)]
    build_pdf("B3_Notices_Proceedings_Settlements/B3.01_Open_SCN_Register/DRC01_SCN_ZD290920240012345.pdf", st)


reg06_pdf(); drc01b_pdf(); scn_pdf()

# ------------------------------------------------------- anomaly key + manifest
write_json("ANOMALY_KEY.json", dict(
    description=("Deliberate defects seeded into this mock dataset. Each one should be caught by "
                 "the corresponding INAFIN reconciliation check. Use this as the expected-results "
                 "fixture for your test suite."),
    seed=SEED, financial_year=FY, count=len(ANOMALIES), anomalies=ANOMALIES))

files = []
for root, _, fnames in os.walk(OUT):
    for f in sorted(fnames):
        fp_ = os.path.join(root, f)
        files.append(dict(path=os.path.relpath(fp_, OUT), bytes=os.path.getsize(fp_)))
write_json("MANIFEST.json", dict(generated_on=str(dt.date.today()), seed=SEED,
                                 entity=dict(legal_name=LEGAL_NAME, pan=PAN, cin=CIN,
                                             gstins=dict(karnataka=GSTIN_KA, maharashtra=GSTIN_MH,
                                                         tamil_nadu=GSTIN_TN, isd=GSTIN_ISD)),
                                 financial_year=FY, file_count=len(files), files=files))
print(f"files: {len(files)}  anomalies: {len(ANOMALIES)}")
print(GSTIN_KA, GSTIN_MH, GSTIN_TN, GSTIN_ISD)
