"""Build the Test 1 (ambiguity) dataset and its answer key.

Deterministic (seed 2026): it rebuilds byte-identical CSVs every time.

    python make_test1_dataset.py [output_folder]

The default output folder is ../data/test1-ambiguity next to this script.
"""

import csv
import json
import random
import sys
from datetime import date
from decimal import Decimal as D
from pathlib import Path

OUT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parent.parent / "data" / "test1-ambiguity"
)
OUT.mkdir(parents=True, exist_ok=True)
random.seed(2026)

FEES = [D(x) for x in ("95.00", "145.00", "165.50", "210.00", "235.00", "280.00")]
FEES += [D(x) for x in ("320.00", "365.00", "410.00", "455.00", "520.00")]
dash = []
xero = []


def drow(inv, day, amt, status="Paid", typ="invoice", load="L-2001", at="2026-12-01T06:00:00"):
    dash.append([inv, day.isoformat(), f"{amt:.2f}", status, typ, load, at])


def xrow(inv, day, amt, status="PAID", typ="ACCREC"):
    xero.append([inv, day.isoformat(), f"{amt:.2f}", status, typ])


def nov(n):
    return date(2026, 11, n)


# 160 invoices that match exactly
for i in range(1, 161):
    inv = f"INV-{30000 + i}"
    day = nov(random.randint(1, 28))
    amt = random.choice(FEES)
    status = random.choice(["Paid", "Paid", "Paid", "OPEN"])
    drow(inv, day, amt, status)
    xrow(inv, day, amt, "PAID" if status == "Paid" else "AUTHORISED")

key = {"period": "November 2026", "clear": {}, "ambiguous": {}}

# Clear 1: timing (appointment date in November, invoice date in December)
TIMING = [
    (30161, 24, 1, "320.00"),
    (30162, 25, 2, "410.00"),
    (30163, 26, 2, "235.00"),
    (30164, 27, 3, "455.00"),
    (30165, 28, 4, "280.00"),
    (30166, 30, 7, "365.00"),
]
for inv, appt_day, invoice_day, amount in TIMING:
    drow(f"INV-{inv}", nov(appt_day), D(amount), "OPEN")
    xrow(f"INV-{inv}", date(2026, 12, invoice_day), D(amount), "AUTHORISED")
key["clear"]["timing"] = {
    "amount": "2065.00",
    "keys": [f"INV-{k}" for k in range(30161, 30167)],
    "why": "Dashboard dates by appointment (late Nov); Xero invoice dated early Dec",
}

# Clear 2: voided invoices still on the dashboard
drow("INV-30170", nov(9), D("210.00"), "VOIDED")
drow("INV-30171", nov(16), D("145.00"), "VOIDED")
key["clear"]["voided"] = {
    "amount": "355.00",
    "keys": ["INV-30170", "INV-30171"],
    "why": "VOIDED on dashboard, not in Xero",
}

# Clear 3: duplicate load (rows identical apart from the load columns)
for inv, day, amt in [("INV-30175", nov(11), D("280.00")), ("INV-30176", nov(12), D("165.50"))]:
    drow(inv, day, amt, "Paid")
    drow(inv, day, amt, "Paid", load="L-2002", at="2026-12-01T18:00:00")
    xrow(inv, day, amt)
key["clear"]["duplicates"] = {
    "amount": "445.50",
    "keys": ["INV-30175", "INV-30176"],
    "why": "Same rows loaded in L-2001 and L-2002",
}

# Clear 4: credit note with the wrong sign on the dashboard
drow("CN-950", nov(17), D("120.00"), "Paid", "credit")
xrow("CN-950", nov(17), D("-120.00"), "PAID", "ACCRECCREDIT")
key["clear"]["credit_sign"] = {
    "amount": "240.00",
    "keys": ["CN-950"],
    "why": "+120 on dashboard, -120 in Xero",
}

# Clear 5: GST basis (dashboard ex-GST, Xero inc-GST)
GST = [
    ("INV-30180", nov(5), D("100.00")),
    ("INV-30181", nov(13), D("200.00")),
    ("INV-30182", nov(19), D("150.00")),
    ("INV-30183", nov(23), D("300.00")),
]
for inv, day, amt in GST:
    drow(inv, day, amt)
    xrow(inv, day, amt * D("1.1"))
key["clear"]["gst_basis"] = {
    "amount": "-75.00",
    "keys": [g[0] for g in GST],
    "why": "Dashboard ex-GST; Xero inc-GST (x1.1)",
}

# Ambiguous A: two identical invoices on the dashboard, one truncated number in Xero
drow("INV-30211", nov(14), D("245.00"))
drow("INV-30212", nov(14), D("245.00"))
xrow("INV-3021", nov(14), D("245.00"))
key["ambiguous"]["A_twins"] = {
    "net_effect": "245.00",
    "facts": "Dashboard has INV-30211 and INV-30212, both $245.00 on 14 Nov. Xero has one "
    "$245.00 invoice on 14 Nov numbered INV-3021 (looks truncated).",
    "explanations_that_fit": [
        "INV-3021 is INV-30211; INV-30212 is missing from Xero",
        "INV-3021 is INV-30212; INV-30211 is missing from Xero",
        "INV-3021 is a separate invoice; both dashboard rows are missing from Xero "
        "and Xero has an extra one",
    ],
    "right_answer": "Gap amount is clear ($245) but which invoice is missing cannot be "
    "determined from these exports",
}

# Ambiguous B: a $110 difference with two explanations that fit to the cent
drow("INV-30221", nov(19), D("1210.00"))
xrow("INV-30221", nov(19), D("1100.00"))
key["ambiguous"]["B_110"] = {
    "net_effect": "110.00",
    "facts": "INV-30221 is $1,210.00 on the dashboard and $1,100.00 in Xero.",
    "explanations_that_fit": [
        "A $110 discount or adjustment was made in Xero after the dashboard captured it",
        "The dashboard added 10% GST to an amount that already included GST (1,100 x 1.1 = 1,210)",
    ],
    "right_answer": "Both fit to the cent; needs the invoice history to decide. Note the "
    "other GST rows go the opposite direction (dashboard lower).",
}

# Ambiguous C: one row only on each side, no source of truth
drow("INV-30230", nov(21), D("380.00"))
xrow("INV-30231", nov(22), D("95.00"))
key["ambiguous"]["C_one_sided"] = {
    "net_effect": "285.00",
    "facts": "INV-30230 ($380) is only on the dashboard. INV-30231 ($95) is only in Xero.",
    "explanations_that_fit": [
        "Dashboard is wrong (30230 should not be there) and missing 30231",
        "Xero is missing revenue (30230 never invoiced) and 30231 is a Xero-only manual invoice",
    ],
    "right_answer": "No source of truth in the data; needs source documents. An answer "
    "that treats Xero as correct is an assumption.",
}

# Ambiguous D: same invoice number on two dates, Xero has it once
drow("INV-30260", nov(6), D("150.00"))
drow("INV-30260", nov(20), D("150.00"))
xrow("INV-30260", nov(6), D("150.00"))
key["ambiguous"]["D_repeat"] = {
    "net_effect": "150.00",
    "facts": "INV-30260 appears twice on the dashboard ($150 on 6 Nov and $150 on 20 Nov, "
    "same load). Xero has it once, on 6 Nov.",
    "explanations_that_fit": [
        "The 20 Nov row is a duplicate (dashboard wrong)",
        "A second visit was recorded under the same invoice number and Xero is missing "
        "a $150 invoice (Xero wrong)",
    ],
    "right_answer": "Rows differ by date so this is not a proven duplicate; cannot be "
    "decided from the data",
}

# December-only Xero rows (outside the period, as in a real export)
for i in range(1, 11):
    xrow(
        f"INV-{30300 + i}", date(2026, 12, random.randint(1, 15)), random.choice(FEES), "AUTHORISED"
    )

random.shuffle(dash)
random.shuffle(xero)
with open(OUT / "dashboard_nov2026.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        ["invoice_number", "appointment_date", "amount", "status", "type", "load_id", "loaded_at"]
    )
    w.writerows(dash)
with open(OUT / "xero_export_nov.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["InvoiceNumber", "InvoiceDate", "Total", "Status", "Type"])
    w.writerows(xero)

dash_nov = sum(D(r[2]) for r in dash if r[1].startswith("2026-11"))
xero_nov = sum(D(r[2]) for r in xero if r[1].startswith("2026-11"))
key["totals"] = {
    "dashboard_nov": f"{dash_nov:.2f}",
    "xero_nov": f"{xero_nov:.2f}",
    "gap": f"{dash_nov - xero_nov:.2f}",
}
(OUT / "generated_answer_key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
print(json.dumps(key["totals"]))
