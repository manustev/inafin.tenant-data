"""
INAFIN mock data generator - CORE
Deterministic master data + transaction universe used by every Category B emitter.
All entities are fictional. GSTINs carry a VALID structural checksum (so format
validators pass) but do not exist on the real GSTN.
"""
import random, json, csv, os, datetime as dt
from decimal import Decimal, ROUND_HALF_UP

SEED = 20240401
random.seed(SEED)

OUT = "/mnt/user-data/outputs/inafin_mock_categoryB"

# ---------------------------------------------------------------- utilities
CH = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum(first14: str) -> str:
    total = 0
    for i, c in enumerate(first14):
        v = CH.index(c)
        f = 2 if (i % 2) else 1
        a = v * f
        total += a // 36 + a % 36
    return CH[(36 - total % 36) % 36]


def mk_gstin(state: str, pan: str, entity: str = "1") -> str:
    base = f"{state}{pan}{entity}Z"
    return base + gstin_checksum(base)


def r2(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def ddmmyyyy(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def fp(period) -> str:            # GSTN filing period MMYYYY
    return period.strftime("%m%Y")


def write_json(relpath, obj):
    p = os.path.join(OUT, relpath)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(obj, f, indent=2)
    return p


def write_csv(relpath, rows, header=None):
    p = os.path.join(OUT, relpath)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not rows:
        return p
    header = header or list(rows[0].keys())
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    return p


# ---------------------------------------------------------------- taxpayer
PAN = "AABCV1234K"
LEGAL_NAME = "VARDHMAN PRECISION INDUSTRIES PRIVATE LIMITED"
TRADE_NAME = "Vardhman Precision"
CIN = "U28999KA2011PTC061234"

GSTIN_KA = mk_gstin("29", PAN, "1")   # Karnataka - HO / factory  (primary)
GSTIN_MH = mk_gstin("27", PAN, "1")   # Maharashtra - branch
GSTIN_TN = mk_gstin("33", PAN, "1")   # Tamil Nadu - depot (registered Aug 2024)
GSTIN_ISD = mk_gstin("29", PAN, "2")  # Karnataka - ISD registration

ENTITY_GSTINS = {
    GSTIN_KA: dict(state="29", state_name="Karnataka", rtype="Regular",
                   eff="01-07-2017", pos_home="29",
                   addr="Plot 44, Phase II, Peenya Industrial Area, Bengaluru, Karnataka, 560058"),
    GSTIN_MH: dict(state="27", state_name="Maharashtra", rtype="Regular",
                   eff="01-07-2017", pos_home="27",
                   addr="Unit 7, Rabale MIDC, Navi Mumbai, Maharashtra, 400701"),
    GSTIN_TN: dict(state="33", state_name="Tamil Nadu", rtype="Regular",
                   eff="12-08-2024", pos_home="33",
                   addr="No 21, Ambattur Industrial Estate, Chennai, Tamil Nadu, 600058"),
    GSTIN_ISD: dict(state="29", state_name="Karnataka", rtype="Input Service Distributor",
                    eff="01-10-2023", pos_home="29",
                    addr="Plot 44, Phase II, Peenya Industrial Area, Bengaluru, Karnataka, 560058"),
}

FY = "2024-25"
PERIODS = [dt.date(2024, m, 1) for m in range(4, 13)] + [dt.date(2025, m, 1) for m in range(1, 4)]


def month_end(p: dt.date) -> dt.date:
    nxt = dt.date(p.year + (p.month == 12), 1 if p.month == 12 else p.month + 1, 1)
    return nxt - dt.timedelta(days=1)


# ---------------------------------------------------------------- customers
def cust(name, state, pan, rtype="Regular", **kw):
    g = mk_gstin(state, pan)
    d = dict(gstin=g, name=name, state=state, rtype=rtype)
    d.update(kw)
    return d


CUSTOMERS = [
    cust("SRIRAM AUTO COMPONENTS PRIVATE LIMITED", "29", "AACCS7712F"),
    cust("KAVERI HYDRAULICS LLP", "29", "AAGFK9012M"),
    cust("DECCAN MACHINE TOOLS PRIVATE LIMITED", "36", "AABCD4455L"),
    cust("NORTHERN VALVE SYSTEMS PRIVATE LIMITED", "07", "AAACN8899J"),
    cust("SAHYADRI ENGINEERING WORKS", "27", "AAEFS1122H"),
    cust("BENGAL PUMPS AND SEALS PRIVATE LIMITED", "19", "AAACB6677K",
         status="Cancelled", cancel_dt="30-09-2024",
         note="Retrospectively cancelled w.e.f. 30-09-2024 - invoices after this date are a finding"),
    cust("GOKUL TRADERS", "29", "ADHPG5566Q", rtype="Composition",
         note="Composition dealer - cannot pass ITC; supply to them is normal B2B for us"),
    cust("KARNATAKA STATE INFRA DEVELOPMENT CORPORATION", "29", "AAAGK3344N",
         rtype="Regular-TDS", note="Government entity - deducts TDS u/s 51, appears in GSTR-7"),
    cust("QUANTUM PRECISION SEZ UNIT PRIVATE LIMITED", "29", "AAFCQ2233R",
         rtype="SEZ", loa="KSEZ/UNIT/2019/0412",
         note="SEZ unit in Aerospace SEZ Devanahalli - zero rated supply"),
]
FOREIGN_CUSTOMERS = [
    dict(name="HELIOS FLUID SYSTEMS GMBH", country="DE", currency="EUR"),
    dict(name="PACIFIC RIM INDUSTRIAL PTE LTD", country="SG", currency="USD"),
    dict(name="ATLAS VALVE CORP", country="US", currency="USD"),
]

# ---------------------------------------------------------------- suppliers
def supp(name, state, pan, **kw):
    d = dict(gstin=mk_gstin(state, pan), name=name, state=state, rtype=kw.pop("rtype", "Regular"))
    d.update(kw)
    return d


SUPPLIERS = [
    supp("BHARAT SPECIAL STEELS LIMITED", "29", "AAACB1199C", cat="input"),
    supp("MYSORE CASTINGS PRIVATE LIMITED", "29", "AABCM5511D", cat="input"),
    supp("INDUS FORGINGS AND ALLOYS PRIVATE LIMITED", "27", "AADCI7733E", cat="input"),
    supp("GAURAV POLYMERS PRIVATE LIMITED", "24", "AACCG9911B", cat="input"),
    supp("SUNRISE TOOLING SOLUTIONS", "33", "AAHFS3355G", cat="input"),
    supp("NIRMAL PACKAGING INDUSTRIES", "29", "AAGFN7799P", cat="input"),
    supp("APEX INDUSTRIAL SERVICES PRIVATE LIMITED", "29", "AABCA2244T", cat="service"),
    supp("SILICON IT INFRA PRIVATE LIMITED", "29", "AAECS8822V", cat="isd",
         note="Common HO service - IT/cloud, distributed via ISD"),
    supp("PRUDENT CONSULTING LLP", "29", "AAJFP4466W", cat="isd",
         note="Common HO service - audit/consulting, distributed via ISD"),
    supp("MAHALAKSHMI ROADLINES PRIVATE LIMITED", "29", "AAECM1177X", cat="gta",
         note="GTA - RCM u/s 9(3), Notification 13/2017"),
    supp("VISHWA ENTERPRISES PRIVATE LIMITED", "29", "AAACV6688Y", cat="input",
         status="Cancelled", cancel_dt="31-12-2024",
         note="Registration cancelled retrospectively w.e.f. 31-12-2024 - ITC after this date is a finding"),
    supp("ORION METALS PRIVATE LIMITED", "27", "AABCO3399Z", cat="input",
         ibc=True, nclt_dt="18-11-2024",
         note="Admitted to CIRP by NCLT Mumbai on 18-11-2024 - 180 day rule exception (contest item B2.1)"),
]

FOREIGN_VENDORS = [
    dict(name="NORDIC CAD SYSTEMS AB", country="SE", svc="Software licence / SaaS subscription"),
    dict(name="ATLAS VALVE CORP", country="US", svc="Technical assistance fee"),
]

IMPORT_SUPPLIERS = [
    dict(name="SHANGHAI PRECISION BEARINGS CO LTD", country="CN"),
    dict(name="KOREA SEALS INDUSTRIAL CO LTD", country="KR"),
]

# ---------------------------------------------------------------- item master
ITEMS = [
    dict(hsn="84819090", desc="Industrial valve components", rate=18, uqc="NOS", price=(850, 4200)),
    dict(hsn="73181500", desc="High tensile fasteners", rate=18, uqc="KGS", price=(180, 640)),
    dict(hsn="84139110", desc="Pump parts and impellers", rate=18, uqc="NOS", price=(1200, 7800)),
    dict(hsn="40169390", desc="Moulded rubber seals and gaskets", rate=12, uqc="NOS", price=(45, 320)),
    dict(hsn="72085190", desc="Hot rolled steel plate (traded)", rate=18, uqc="MTS", price=(52000, 68000)),
    dict(hsn="998719", desc="Maintenance and repair of machinery (service)", rate=18, uqc="OTH", price=(15000, 240000)),
]
INPUT_ITEMS = [
    dict(hsn="72283000", desc="Alloy steel bars", rate=18, uqc="MTS", price=(58000, 74000)),
    dict(hsn="73251000", desc="Iron castings", rate=18, uqc="KGS", price=(90, 210)),
    dict(hsn="39269099", desc="Plastic mouldings", rate=18, uqc="KGS", price=(120, 340)),
    dict(hsn="48191010", desc="Corrugated cartons", rate=12, uqc="NOS", price=(22, 95)),
    dict(hsn="998873", desc="Job work - machining services", rate=12, uqc="OTH", price=(18000, 165000)),
    dict(hsn="997212", desc="Rental of immovable property", rate=18, uqc="OTH", price=(180000, 420000)),
    dict(hsn="998313", desc="IT consulting and cloud services", rate=18, uqc="OTH", price=(90000, 310000)),
    dict(hsn="998222", desc="Audit and accounting services", rate=18, uqc="OTH", price=(60000, 250000)),
    dict(hsn="996511", desc="Road transport of goods (GTA)", rate=5, uqc="OTH", price=(24000, 96000)),
]

# ---------------------------------------------------------------- anomalies
# Every deliberate defect is registered here and dumped to ANOMALY_KEY.json
ANOMALIES = []


def anomaly(code, doc_refs, period, description, expected_check):
    ANOMALIES.append(dict(anomaly_id=f"MOCK-{len(ANOMALIES)+1:02d}", code=code,
                          doc_refs=doc_refs, period=period,
                          description=description, expected_detection=expected_check))


# ---------------------------------------------------------------- transactions
def split_tax(pos, supplier_state, txval, rate):
    """Return (iamt, camt, samt) for a taxable value at a rate."""
    tax = r2(txval * rate / 100)
    if pos == supplier_state:
        half = r2(tax / 2)
        return 0.0, half, r2(tax - half)
    return tax, 0.0, 0.0


class Universe:
    """Builds the full outward + inward transaction set once; all emitters read it."""

    def __init__(self):
        self.out = []      # outward invoices (all GSTINs)
        self.cdn = []      # credit / debit notes
        self.inward = []   # inward invoices appearing in GSTR-2B / 2A
        self.rcm = []      # RCM inward (GTA, import of services)
        self.imports = []  # bill of entry imports
        self.advances = []
        self._build()

    # ---- outward -------------------------------------------------
    def _build(self):
        rng = random.Random(SEED)
        # weighted customer pool - SEZ and government customers are occasional
        pool = CUSTOMERS[:6] * 3 + [CUSTOMERS[6]] * 2 + [CUSTOMERS[7]] * 2 + [CUSTOMERS[8]]
        seq = {GSTIN_KA: 0, GSTIN_MH: 0, GSTIN_TN: 0}
        for p in PERIODS:
            me = month_end(p)
            for gstin, n_b2b in ((GSTIN_KA, 16), (GSTIN_MH, 6),
                                 (GSTIN_TN, 3 if p >= dt.date(2024, 9, 1) else 0)):
                if n_b2b == 0:
                    continue
                st = ENTITY_GSTINS[gstin]["state"]
                pref = {GSTIN_KA: "VPI/KA/24-25/", GSTIN_MH: "VPI/MH/24-25/", GSTIN_TN: "VPI/TN/24-25/"}[gstin]
                for _ in range(n_b2b):
                    seq[gstin] += 1
                    c = rng.choice(pool)
                    if c["rtype"] == "Regular-TDS" and rng.random() > 0.35:
                        c = CUSTOMERS[0]
                    idt = dt.date(p.year, p.month, rng.randint(1, me.day))
                    it = rng.choice(ITEMS)
                    up = rng.randint(*it["price"])
                    qty = 1 if it["uqc"] == "OTH" else (rng.randint(6, 90) if up < 5000
                                                        else rng.randint(2, 14))
                    txval = r2(qty * up)
                    i, cg, sg = split_tax(c["state"], st, txval, it["rate"])
                    self.out.append(dict(
                        gstin=gstin, inum=f"{pref}{seq[gstin]:04d}", idt=idt,
                        ctin=c["gstin"], cname=c["name"], pos=c["state"], ctype=c["rtype"],
                        cat="B2B" if c["rtype"] != "SEZ" else "SEZWOP",
                        hsn=it["hsn"], desc=it["desc"], uqc=it["uqc"], qty=qty, rate=it["rate"],
                        txval=txval, iamt=i, camt=cg, samt=sg, csamt=0.0,
                        val=r2(txval + i + cg + sg), rchrg="N",
                        inv_typ="SEWOP" if c["rtype"] == "SEZ" else "R"))
                # exports only from KA
                if gstin == GSTIN_KA:
                    for k in range(2):
                        seq[gstin] += 1
                        fc = rng.choice(FOREIGN_CUSTOMERS)
                        idt = dt.date(p.year, p.month, rng.randint(2, me.day))
                        it = rng.choice(ITEMS[:5])
                        txval = r2(rng.randint(300000, 950000))
                        self.out.append(dict(
                            gstin=gstin, inum=f"{pref}{seq[gstin]:04d}", idt=idt,
                            ctin=None, cname=fc["name"], pos="96", ctype="Export",
                            cat="EXPWOP", hsn=it["hsn"], desc=it["desc"], uqc=it["uqc"],
                            qty=1, rate=it["rate"], txval=txval, iamt=0.0, camt=0.0, samt=0.0,
                            csamt=0.0, val=txval, rchrg="N", inv_typ="EXPWOP",
                            country=fc["country"], currency=fc["currency"]))
                # B2CL (inter-state unregistered > 2.5 lakh) from KA only
                if gstin == GSTIN_KA:
                    for k in range(2):
                        seq[gstin] += 1
                        pos = rng.choice(["06", "08", "24", "32", "36"])
                        idt = dt.date(p.year, p.month, rng.randint(1, me.day))
                        it = rng.choice(ITEMS)
                        txval = r2(rng.randint(260000, 900000))
                        i, cg, sg = split_tax(pos, st, txval, it["rate"])
                        self.out.append(dict(
                            gstin=gstin, inum=f"{pref}{seq[gstin]:04d}", idt=idt, ctin=None,
                            cname="Unregistered customer", pos=pos, ctype="URD", cat="B2CL",
                            hsn=it["hsn"], desc=it["desc"], uqc=it["uqc"], qty=1, rate=it["rate"],
                            txval=txval, iamt=i, camt=cg, samt=sg, csamt=0.0,
                            val=r2(txval + i), rchrg="N", inv_typ="R"))
                # B2CS aggregate (counter sales) - one synthetic line per rate
                for rate in (18, 12):
                    txval = r2(rng.randint(120000, 640000) * (1 if gstin == GSTIN_KA else 0.4))
                    i, cg, sg = split_tax(st, st, txval, rate)
                    self.out.append(dict(
                        gstin=gstin, inum=None, idt=me, ctin=None, cname="B2C aggregate",
                        pos=st, ctype="URD", cat="B2CS", hsn=ITEMS[0]["hsn"], desc="B2C counter sales",
                        uqc="NOS", qty=1, rate=rate, txval=txval, iamt=i, camt=cg, samt=sg,
                        csamt=0.0, val=r2(txval + i + cg + sg), rchrg="N", inv_typ="OE"))

            # credit notes (against KA invoices of an earlier month)
            for k in range(2):
                cn_pool = [o for o in self.out if o["gstin"] == GSTIN_KA and o["cat"] == "B2B"
                           and o["idt"] < p]
                if not cn_pool:
                    continue
                orig = rng.choice(cn_pool)
                ratio = rng.choice([0.05, 0.1, 0.15])
                txval = r2(orig["txval"] * ratio)
                i, cg, sg = split_tax(orig["pos"], "29", txval, orig["rate"])
                self.cdn.append(dict(
                    gstin=GSTIN_KA, ntty="C", nt_num=f"VPI/KA/CN/24-25/{len(self.cdn)+1:03d}",
                    nt_dt=dt.date(p.year, p.month, rng.randint(5, 26)),
                    inum=orig["inum"], idt=orig["idt"], ctin=orig["ctin"], cname=orig["cname"],
                    pos=orig["pos"], rate=orig["rate"], txval=txval, iamt=i, camt=cg, samt=sg,
                    csamt=0.0, val=r2(txval + i + cg + sg),
                    rsn="Rate difference / short supply"))

            # advances received (services) - GSTR-1 Table 11
            for k in range(2):
                c = rng.choice(CUSTOMERS[:5])
                amt = r2(rng.randint(150000, 900000))
                self.advances.append(dict(gstin=GSTIN_KA, period=p, pos=c["state"], ctin=c["gstin"],
                                          rate=18, ad_amt=amt,
                                          **dict(zip(("iamt", "camt", "samt"),
                                                     split_tax(c["state"], "29", amt, 18)))))

            # ---- inward ------------------------------------------
            for gstin in (GSTIN_KA, GSTIN_MH):
                st = ENTITY_GSTINS[gstin]["state"]
                for _ in range(14 if gstin == GSTIN_KA else 5):
                    s = rng.choice([x for x in SUPPLIERS if x["cat"] in ("input", "service")])
                    it = rng.choice(INPUT_ITEMS[:6])
                    idt = dt.date(p.year, p.month, rng.randint(1, me.day))
                    up2 = rng.randint(*it["price"])
                    qty = 1 if it["uqc"] == "OTH" else (rng.randint(8, 70) if up2 < 5000
                                                        else rng.randint(2, 16))
                    txval = r2(qty * up2)
                    i, cg, sg = split_tax(st, s["state"], txval, it["rate"])
                    itc_avl = "Y"
                    rsn = ""
                    if it["hsn"] == "997212" and rng.random() < 0.15:
                        itc_avl, rsn = "N", "C"      # ineligible - Sec 17(5)
                    self.inward.append(dict(
                        rec_gstin=gstin, ctin=s["gstin"], sname=s["name"], inum=f"{s['name'][:3].upper()}/24-25/{rng.randint(1000,9999)}",
                        idt=idt, period=p, hsn=it["hsn"], desc=it["desc"], rate=it["rate"],
                        txval=txval, iamt=i, camt=cg, samt=sg, csamt=0.0,
                        val=r2(txval + i + cg + sg), itc_avl=itc_avl, rsn=rsn,
                        supplier_state=s["state"], amended=False))
                # RCM - GTA every month, import of services in some months
                gta = [x for x in SUPPLIERS if x["cat"] == "gta"][0]
                txval = r2(rng.randint(24000, 96000))
                self.rcm.append(dict(rec_gstin=gstin, period=p, kind="GTA", ctin=gta["gstin"],
                                     sname=gta["name"], inum=f"MRL/CN/{rng.randint(20000,29999)}",
                                     idt=dt.date(p.year, p.month, rng.randint(3, 27)),
                                     rate=5, txval=txval,
                                     **dict(zip(("iamt", "camt", "samt"),
                                                split_tax(st, st, txval, 5)))))
            if p.month in (5, 8, 11, 2):
                fv = rng.choice(FOREIGN_VENDORS)
                txval = r2(rng.randint(400000, 2200000))
                self.rcm.append(dict(rec_gstin=GSTIN_KA, period=p, kind="IMPS", ctin=None,
                                     sname=fv["name"], inum=f"INV-{fv['country']}-{rng.randint(100,999)}",
                                     idt=dt.date(p.year, p.month, rng.randint(3, 25)),
                                     rate=18, txval=txval, iamt=r2(txval * .18), camt=0.0, samt=0.0))
            # imports of goods (BoE)
            if p.month % 2 == 0:
                imp = rng.choice(IMPORT_SUPPLIERS)
                assess = r2(rng.randint(900000, 4500000))
                bcd = r2(assess * 0.075)
                igst = r2((assess + bcd) * 0.18)
                self.imports.append(dict(
                    rec_gstin=GSTIN_KA, period=p, boe_no=f"{rng.randint(2000000,7999999)}",
                    boe_dt=dt.date(p.year, p.month, rng.randint(2, 25)),
                    port="INMAA1" if rng.random() < .5 else "INBLR4",
                    supplier=imp["name"], country=imp["country"], hsn="84829900",
                    assessable_value=assess, bcd=bcd, igst=igst, cess=0.0))

        self._normalise()
        self._inject_anomalies()

    # ---- volume normalisation -----------------------------------
    def _normalise(self):
        """Scale each month to a realistic run-rate so monthly totals are smooth
        (raw random draws swing 20x, which no auditor would believe)."""
        rng = random.Random(SEED + 5)
        target_out = {GSTIN_KA: 165_000_000 / 12, GSTIN_MH: 48_000_000 / 12,
                      GSTIN_TN: 14_000_000 / 12}
        for gstin in target_out:
            for p in PERIODS:
                rows = [o for o in self.out if o["gstin"] == gstin
                        and (o["idt"].month, o["idt"].year) == (p.month, p.year)]
                cur = sum(o["txval"] for o in rows)
                if not cur:
                    continue
                tgt = target_out[gstin] * rng.uniform(0.86, 1.16)
                f = tgt / cur
                st = ENTITY_GSTINS[gstin]["state"]
                for o in rows:
                    o["txval"] = r2(o["txval"] * f)
                    if o["cat"] == "EXPWOP" or o["inv_typ"] == "SEWOP":
                        o["iamt"] = o["camt"] = o["samt"] = 0.0
                    else:
                        o["iamt"], o["camt"], o["samt"] = split_tax(o["pos"], st, o["txval"], o["rate"])
                    o["val"] = r2(o["txval"] + o["iamt"] + o["camt"] + o["samt"])
                    if o["cat"] == "EXPWOP":
                        o["val"] = o["txval"]
                # credit notes of this month follow the same scale
                for c in [x for x in self.cdn if x["gstin"] == gstin
                          and (x["nt_dt"].month, x["nt_dt"].year) == (p.month, p.year)]:
                    c["txval"] = r2(c["txval"] * f)
                    c["iamt"], c["camt"], c["samt"] = split_tax(c["pos"], st, c["txval"], c["rate"])
                    c["val"] = r2(c["txval"] + c["iamt"] + c["camt"] + c["samt"])
                # inward at ~62% of outward for the same GSTIN/month
                inw = [x for x in self.inward if x["rec_gstin"] == gstin and x["period"] == p]
                cin = sum(x["txval"] for x in inw)
                if cin:
                    fi = (tgt * rng.uniform(0.56, 0.68)) / cin
                    for x in inw:
                        x["txval"] = r2(x["txval"] * fi)
                        x["iamt"], x["camt"], x["samt"] = split_tax(st, x["supplier_state"],
                                                                   x["txval"], x["rate"])
                        x["val"] = r2(x["txval"] + x["iamt"] + x["camt"] + x["samt"])

    # ---- deliberate defects -------------------------------------
    def _inject_anomalies(self):
        # 1. supplier amendment: Nov-24 invoice value reduced by supplier in Jan-25 2B
        cand = [x for x in self.inward if x["period"] == dt.date(2024, 11, 1)
                and x["rec_gstin"] == GSTIN_KA][3]
        cand["amended"] = True
        cand["amend_period"] = dt.date(2025, 1, 1)
        cand["amend_txval"] = r2(cand["txval"] * 0.72)
        anomaly("B1.04-AMEND", ["B1.03", "B1.04", "B1.05"], "112024",
                f"Supplier {cand['sname']} amended invoice {cand['inum']} in Jan-25; taxable value "
                f"reduced from {cand['txval']} to {cand['amend_txval']}. ITC already availed in Nov-24 3B.",
                "Excess ITC availed = tax differential; reversal with interest u/s 50(3) expected.")
        # 2. invoice to a customer whose GSTIN was cancelled w.e.f. 30-09-2024
        for o in self.out:
            if o["ctin"] == CUSTOMERS[5]["gstin"] and o["idt"] > dt.date(2024, 9, 30):
                o["flag_cancelled_ctin"] = True
        anomaly("B2.07-CANCEL-CUST", ["B2.02", "B2.07"], "FY2024-25",
                f"Outward invoices raised on {CUSTOMERS[5]['name']} ({CUSTOMERS[5]['gstin']}) after its "
                "registration was cancelled w.e.f. 30-09-2024.",
                "GSTIN status at invoice date check must flag these; buyer ITC is void.")
        # 3. ITC taken from a supplier cancelled w.e.f. 31-12-2024
        for x in self.inward:
            if x["ctin"] == SUPPLIERS[10]["gstin"] and x["idt"] > dt.date(2024, 12, 31):
                x["flag_cancelled_supplier"] = True
        anomaly("B2.03-CANCEL-SUPP", ["B2.03", "B2.07", "B1.03"], "FY2024-25",
                f"ITC availed on invoices of {SUPPLIERS[10]['name']} dated after cancellation (31-12-2024).",
                "ITC ineligible; must be reversed. Status-at-invoice-date test, not current status.")
        return


UNI = Universe()
