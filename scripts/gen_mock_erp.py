#!/usr/bin/env python3
"""Generate a mock ERP export for any archetype-1 document type.

The real client documents are not available yet, and for STRUCTURED Stream B
data that is a much smaller problem than it looks: an ERP export's schema is a
contract *we* specify, not a layout we have to discover. So a synthetic file
here has real fidelity, in a way a synthetic scanned LUT never would.

    python scripts/gen_mock_erp.py PURCHASE_REGISTER --docs 5 --lines 3
    python scripts/gen_mock_erp.py BILL_OF_ENTRY --out tests/fixtures/boe.csv

Deterministic: the same arguments produce byte-identical output, so a generated
fixture can be committed and diffed like any other file.

**The trap this leaves open, stated plainly.** The generator reads the same
`field_contract` the validator does, so a WRONG contract produces a file that
validates perfectly and proves nothing — the two are wrong in the same
direction. That is why `tests/fixtures/` also holds hand-written files that were
never produced by this script. When the two disagree, the disagreement is the
finding. Do not "fix" a hand-written fixture by regenerating it.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.silver.contract import (  # noqa: E402
    Attribute,
    Counterparty,
    FieldContract,
    parse_contract,
)

CSV_PATH = ROOT / "registry" / "document_types.csv"

CORE_COLUMNS = [
    "doc_number", "counterparty_gstin", "counterparty_name", "doc_date", "currency",
    "line_number", "hsn_sac", "description", "quantity", "uom", "unit_price",
    "taxable_value", "gst_rate", "cgst_amount", "sgst_amount", "igst_amount",
    "cess_amount", "itc_amount",
]

_STATE_CODES = ("27", "29", "06", "33", "07")
_HSN = ("84713010", "85176290", "39269099", "72142090", "94036000")
_UOM = ("NOS", "KGS", "MTR", "PCS")


def load_contract(doc_type_code: str) -> FieldContract:
    for row in csv.DictReader(CSV_PATH.open()):
        if row["doc_type_code"] == doc_type_code and row["field_contract"]:
            return parse_contract(row["field_contract"])
    raise SystemExit(
        f"no field_contract for {doc_type_code!r} in {CSV_PATH.relative_to(ROOT)}"
    )


def _gstin(rng: random.Random) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return rng.choice(_STATE_CODES) + "".join(rng.choices(alphabet, k=13))


def _attr_value(attr: Attribute, rng: random.Random, base: dt.date, i: int) -> str:
    match attr.type:
        case "date":
            return (base + dt.timedelta(days=rng.randint(1, 180))).isoformat()
        case "money":
            return f"{rng.randint(100, 90000)}.00"
        case "qty":
            return f"{rng.randint(1, 500)}.000"
        case "rate":
            return f"{rng.choice((0, 5, 12, 18, 28))}.00"
        case "int":
            return str(rng.randint(1, 9999))
        case "gstin":
            return _gstin(rng)
        case "hsn":
            return rng.choice(_HSN)
        case "hash64":
            return "".join(rng.choices("0123456789abcdef", k=64))
        case _:
            return f"{attr.name.upper()}-{i:04d}"


def generate(
    contract: FieldContract,
    *,
    docs: int,
    lines: int,
    seed: int,
    prefix: str,
) -> str:
    # Seeded and reproducible is the REQUIREMENT here, not a compromise: a
    # committed fixture has to regenerate byte-identically or it cannot be
    # diffed. Nothing this produces is a secret, an identifier, or a key.
    rng = random.Random(seed)  # noqa: S311
    base = dt.date(2026, 4, 1)

    columns = list(CORE_COLUMNS)
    columns += [a.name for a in contract.doc_attributes]
    columns += [a.name for a in contract.line_attributes]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()

    for d in range(1, docs + 1):
        # FOREIGN means there is no GSTIN to have — an import Bill of Entry
        # names an overseas supplier. Emitting one anyway would produce a file
        # the validator is right to reject.
        match contract.counterparty:
            case Counterparty.FOREIGN:
                gstin, name = "", f"Overseas Supplier {d} Pte Ltd"
            case Counterparty.REQUIRED:
                gstin, name = _gstin(rng), f"Counterparty {d} Private Limited"
            case _:
                # OPTIONAL exercises BOTH branches — every fourth document is
                # B2C, which is the case a fixture that always supplies a GSTIN
                # would never reach.
                gstin = "" if d % 4 == 0 else _gstin(rng)
                name = f"Counterparty {d} Private Limited" if gstin else ""

        doc_date = base + dt.timedelta(days=rng.randint(0, 29))
        doc_attrs = {
            a.name: _attr_value(a, rng, doc_date, d) for a in contract.doc_attributes
        }

        for n in range(1, lines + 1):
            qty = rng.randint(1, 100)
            unit_price = rng.randint(100, 5000)
            taxable = qty * unit_price
            rate = rng.choice((5, 12, 18, 28))
            tax = round(taxable * rate / 100, 2)

            # Intra-state splits CGST/SGST, inter-state is IGST. Never both —
            # the tax-head CHECK in tenant 007 rejects that, and a generator
            # that could emit it would be producing files the database refuses.
            interstate = rng.random() < 0.5
            row = {
                "doc_number": f"{prefix}-{d:04d}",
                "counterparty_gstin": gstin,
                "counterparty_name": name,
                "doc_date": doc_date.isoformat(),
                "currency": "INR",
                "line_number": n,
                "hsn_sac": rng.choice(_HSN),
                "description": f"Line item {n} of document {d}",
                "quantity": f"{qty}.000",
                "uom": rng.choice(_UOM),
                "unit_price": f"{unit_price}.0000",
                "taxable_value": f"{taxable}.00",
                "gst_rate": f"{rate}.00",
                "cgst_amount": "0.00" if interstate else f"{tax / 2:.2f}",
                "sgst_amount": "0.00" if interstate else f"{tax / 2:.2f}",
                "igst_amount": f"{tax:.2f}" if interstate else "0.00",
                "cess_amount": "0.00",
                "itc_amount": f"{tax:.2f}",
            }
            row.update(doc_attrs)
            for a in contract.line_attributes:
                row[a.name] = _attr_value(a, rng, doc_date, n)
            writer.writerow(row)

    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc_type_code", help="registry code, e.g. PURCHASE_REGISTER")
    ap.add_argument("--docs", type=int, default=5)
    ap.add_argument("--lines", type=int, default=2)
    ap.add_argument("--seed", type=int, default=20260804)
    ap.add_argument("--prefix", default=None, help="document number prefix")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    contract = load_contract(args.doc_type_code)
    text = generate(
        contract,
        docs=args.docs,
        lines=args.lines,
        seed=args.seed,
        prefix=args.prefix or args.doc_type_code[:8],
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(
            f"wrote {args.out} — {args.docs} document(s) x {args.lines} line(s), "
            f"direction={contract.direction}, counterparty={contract.counterparty}"
        )
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
