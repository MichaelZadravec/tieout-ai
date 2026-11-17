"""Synthetic "dashboard vs Xero" pair for October 2026 with known injected issues.

Side A is a practice dashboard export (October appointments, amounts as the
dashboard shows them). Side B is a Xero invoice export covering October and
November, so invoices raised in November for October appointments are visible.

Injected discrepancies, each with its own invoice numbers:

- timing:        58 invoices with an October appointment and a November invoice
                 date, $26,900 in total (dashboard only, for an October period)
- status:        3 voided invoices the dashboard still counts, $600
- tax_basis:     12 invoices the dashboard shows ex GST; Xero shows inc GST (10%)
- duplicates:    4 invoices exported twice by the dashboard
- sign_flip:     3 credit notes the dashboard shows as positive amounts
- missing_credit_notes: 2 Xero credit notes the dashboard does not include

Everything is driven by a fixed seed, so the output is byte-for-byte stable.

Usage: python fixtures/generate.py [OUT_DIR]   (default: fixtures/data)
`recon demo` uses it too, so it ships inside the package.
"""

from __future__ import annotations

import csv
import json
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

SEED = 20261031
DASHBOARD_FILE = "dashboard_oct2026.csv"
XERO_FILE = "xero_oct_nov2026.csv"
MANIFEST_FILE = "manifest.json"

DASHBOARD_COLUMNS = ["invoice_number", "appointment_date", "amount", "status", "type"]
XERO_COLUMNS = ["InvoiceNumber", "InvoiceDate", "Total", "Status", "Type"]

OCTOBER = (date(2026, 10, 1), date(2026, 10, 31))
GST = Decimal("0.10")

TIMING_COUNT = 58
TIMING_TOTAL = Decimal(26900)
VOIDED_AMOUNTS = [Decimal(150), Decimal(200), Decimal(250)]

# The hypotheses an analyst would try for this pair, one per injected issue.
PLAN = [
    ["timing", {"max_shift_days": 31}],
    ["status_filter", {"side": "a", "status_column": "status", "statuses": ["VOIDED"]}],
    ["tax_basis", {"rate": "0.10", "inclusive_side": "b"}],
    ["duplicates", {"side": "a"}],
    ["sign_credit", {"mode": "sign_flip"}],
    [
        "sign_credit",
        {
            "mode": "missing_credit_notes",
            "side": "b",
            "type_column": "Type",
            "credit_types": ["ACCRECCREDIT"],
        },
    ],
]


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _october_day(rng: random.Random, first: int = 1, last: int = 31) -> date:
    return date(2026, 10, rng.randint(first, last))


def _timing_amounts(rng: random.Random) -> list[Decimal]:
    """58 whole-dollar amounts that sum to exactly $26,900."""
    choices = [300, 350, 400, 450, 500, 550, 600]
    while True:
        amounts = [Decimal(rng.choice(choices)) for _ in range(TIMING_COUNT - 1)]
        last = TIMING_TOTAL - sum(amounts)
        if Decimal(200) <= last <= Decimal(900):
            return [*amounts, last]


def generate(out_dir: Path) -> dict:
    rng = random.Random(SEED)
    dashboard: list[list[str]] = []
    xero: list[list[str]] = []
    manifest: dict[str, dict] = {}
    numbers = iter(range(10001, 20000))

    def invoice_number() -> str:
        return f"INV-{next(numbers)}"

    def record(name: str, keys: list[str], amount: Decimal) -> None:
        manifest[name] = {"keys": keys, "count": len(keys), "amount": _money(amount)}

    # Invoices both sides agree on.
    for _ in range(300):
        number, day = invoice_number(), _october_day(rng)
        amount = Decimal(rng.randint(80, 900)) + Decimal(rng.choice([0, 50])) / 100
        status = rng.choice(["PAID", "PAID", "PAID", "OPEN"])
        dashboard.append([number, day.isoformat(), _money(amount), status, "invoice"])
        xero.append(
            [
                number,
                day.isoformat(),
                _money(amount),
                "PAID" if status == "PAID" else "AUTHORISED",
                "ACCREC",
            ]
        )

    # Genuine November invoices: outside the period on Xero, absent from the dashboard.
    for _ in range(40):
        number = invoice_number()
        day = date(2026, 11, rng.randint(1, 30))
        amount = Decimal(rng.randint(80, 900))
        xero.append([number, day.isoformat(), _money(amount), "AUTHORISED", "ACCREC"])

    # timing: October appointment, invoiced 3 to 12 days later in November.
    keys = []
    for amount in _timing_amounts(rng):
        number = invoice_number()
        appointment = _october_day(rng, 20, 31)
        invoiced = appointment + timedelta(days=rng.randint(3, 12))
        if invoiced.month == 10:
            # Only reachable for appointments on or before 28 Oct, so the shift stays >= 4 days.
            invoiced = date(2026, 11, 1) + timedelta(days=rng.randint(0, 4))
        dashboard.append([number, appointment.isoformat(), _money(amount), "PAID", "invoice"])
        xero.append([number, invoiced.isoformat(), _money(amount), "PAID", "ACCREC"])
        keys.append(number)
    record("timing", keys, TIMING_TOTAL)

    # status: voided invoices the dashboard still counts; Xero's export omits them.
    keys = []
    for amount in VOIDED_AMOUNTS:
        number = invoice_number()
        dashboard.append(
            [number, _october_day(rng).isoformat(), _money(amount), "VOIDED", "invoice"]
        )
        keys.append(number)
    record("status_filter", keys, sum(VOIDED_AMOUNTS, Decimal(0)))

    # tax_basis: dashboard ex GST, Xero inc GST. Gap contribution is ex - inc.
    keys, total = [], Decimal(0)
    for _ in range(12):
        number, day = invoice_number(), _october_day(rng)
        ex = Decimal(rng.randint(5, 60) * 10)
        inc = ex * (1 + GST)
        dashboard.append([number, day.isoformat(), _money(ex), "PAID", "invoice"])
        xero.append([number, day.isoformat(), _money(inc), "PAID", "ACCREC"])
        keys.append(number)
        total += ex - inc
    record("tax_basis", keys, total)

    # duplicates: the dashboard exports these rows twice.
    keys, total = [], Decimal(0)
    for _ in range(4):
        number, day = invoice_number(), _october_day(rng)
        amount = Decimal(rng.randint(100, 500))
        row = [number, day.isoformat(), _money(amount), "PAID", "invoice"]
        dashboard.extend([row, list(row)])
        xero.append([number, day.isoformat(), _money(amount), "PAID", "ACCREC"])
        keys.append(number)
        total += amount
    record("duplicates", keys, total)

    # sign_flip: credit notes positive on the dashboard, negative in Xero.
    keys, total = [], Decimal(0)
    credit_numbers = iter(range(501, 600))
    for _ in range(3):
        number, day = f"CN-{next(credit_numbers)}", _october_day(rng)
        amount = Decimal(rng.randint(40, 200))
        dashboard.append([number, day.isoformat(), _money(amount), "PAID", "credit"])
        xero.append([number, day.isoformat(), _money(-amount), "PAID", "ACCRECCREDIT"])
        keys.append(number)
        total += 2 * amount
    record("sign_flip", keys, total)

    # missing_credit_notes: in Xero only. Gap contribution is -(negative amount).
    keys, total = [], Decimal(0)
    for _ in range(2):
        number, day = f"CN-{next(credit_numbers)}", _october_day(rng)
        amount = Decimal(rng.randint(40, 200))
        xero.append([number, day.isoformat(), _money(-amount), "AUTHORISED", "ACCRECCREDIT"])
        keys.append(number)
        total += amount
    record("missing_credit_notes", keys, total)

    rng.shuffle(dashboard)
    rng.shuffle(xero)

    gap = sum((Decimal(entry["amount"]) for entry in manifest.values()), Decimal(0))
    summary = {
        "period": {"start": OCTOBER[0].isoformat(), "end": OCTOBER[1].isoformat()},
        "expected_gap": _money(gap),
        "injected": manifest,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / DASHBOARD_FILE, DASHBOARD_COLUMNS, dashboard)
    _write_csv(out_dir / XERO_FILE, XERO_COLUMNS, xero)
    (out_dir / MANIFEST_FILE).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return summary


def _write_csv(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def main(default_out: Path) -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default_out
    result = generate(target)
    print(f"Wrote fixtures to {target} (expected gap {result['expected_gap']})")
