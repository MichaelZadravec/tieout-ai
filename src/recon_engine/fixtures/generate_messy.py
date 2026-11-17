"""A messy "dashboard vs Xero" pair whose honest residual is not zero.

Same shape as fixtures/generate.py, but with the kind of dirt real exports have.
The manifest records the hypotheses an analyst would reasonably try (`plan`),
exactly which keys each check may claim (`claims`), and every discrepancy that
must stay unexplained (`residual`), with the reason.

Must be claimed:
- timing:        10 clean timing invoices (October appointment, November invoice)
- status_filter: voided invoices whose status is exactly "VOIDED" or "Voided"
- tax_basis:     6 ex/inc GST pairs that round correctly to the cent, plus 2 that
                 are 1 cent off (claimable only with rounding_tolerance 0.01)
- duplicates:    3 near-duplicates differing only in load_id/loaded_at
                 (claimable only with compare_columns that leave those out)
- sign_flip:     1 credit note with opposite signs
- rounding:      1 invoice 1 cent apart (rounding check, tolerance $0.01)

Must stay unexplained:
- voided rows whose status is "voided " or " VOIDED" (case/whitespace variants
  the plan does not name)
- a dashboard-only row with no status
- an invoice number with a trailing space on the dashboard (two items, net $0)
- a dashboard row with no invoice number
- 2 compound invoices: timing AND GST at once (neither check alone can prove them)
- 1 repeated invoice whose copies have different appointment dates (not a duplicate)
- 1 dashboard row with an empty amount: a data-quality item, so Xero's row for that
  invoice is unmatched

Usage: python fixtures/generate_messy.py [OUT_DIR]   (default: fixtures/data)
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from recon_engine.fixtures.generate import GST, OCTOBER, XERO_COLUMNS, _money, _write_csv

SEED = 20261104
DASHBOARD_FILE = "dashboard_messy_oct2026.csv"
XERO_FILE = "xero_messy_oct_nov2026.csv"
MANIFEST_FILE = "manifest_messy.json"

DASHBOARD_COLUMNS = [
    "invoice_number",
    "appointment_date",
    "amount",
    "status",
    "type",
    "load_id",
    "loaded_at",
]
LOAD = ("L-1001", "2026-11-01T06:00:00")
RELOAD = ("L-1002", "2026-11-02T06:00:00")

PLAN = [
    ["timing", {"max_shift_days": 31}],
    ["status_filter", {"side": "a", "status_column": "status", "statuses": ["VOIDED", "Voided"]}],
    ["tax_basis", {"rate": "0.10", "inclusive_side": "b", "rounding_tolerance": "0.01"}],
    [
        "duplicates",
        {
            "side": "a",
            "compare_columns": ["invoice_number", "appointment_date", "amount", "status", "type"],
        },
    ],
    ["sign_credit", {"mode": "sign_flip"}],
    ["rounding", {"tolerance": "0.01"}],
]
CLAIM_LABELS = ["timing", "status_filter", "tax_basis", "duplicates", "sign_flip", "rounding"]


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def generate_messy(out_dir: Path) -> dict:
    rng = random.Random(SEED)
    dashboard: list[list[str | None]] = []
    xero: list[list[str]] = []
    claims: dict[str, dict] = {label: {"keys": [], "amount": Decimal(0)} for label in CLAIM_LABELS}
    residual: list[dict] = []
    numbers = iter(range(20001, 30000))

    def number() -> str:
        return f"INV-{next(numbers)}"

    def day() -> date:
        return date(2026, 10, rng.randint(1, 31))

    def dash(key, when, amount, status="PAID", kind="invoice", load=LOAD) -> None:
        dashboard.append([key, when, amount, status, kind, *load])

    def claim(label: str, key: str, amount: Decimal) -> None:
        claims[label]["keys"].append(key)
        claims[label]["amount"] += amount

    def unexplained(key, kind: str, amount: Decimal, reason: str) -> None:
        residual.append({"key": key, "kind": kind, "amount": _money(amount), "reason": reason})

    # Agreed invoices, with harmless dirt: lower-case statuses, padded amounts.
    for _ in range(150):
        key, when = number(), day().isoformat()
        amount = Decimal(rng.randint(80, 900)) + Decimal(rng.choice([0, 25, 50])) / 100
        status = rng.choice(["PAID", "paid", "Paid", "OPEN"])
        shown = rng.choice([_money(amount), f" {_money(amount)}", f"{_money(amount)} "])
        dash(key, when, shown, status)
        xero.append([key, when, _money(amount), "PAID", "ACCREC"])

    # Genuine November invoices, outside the period.
    for _ in range(10):
        when = date(2026, 11, rng.randint(1, 30)).isoformat()
        xero.append([number(), when, _money(Decimal(rng.randint(80, 900))), "AUTHORISED", "ACCREC"])

    # Claimable: clean timing.
    for _ in range(10):
        key, amount = number(), Decimal(rng.randint(200, 600))
        appointment = date(2026, 10, rng.randint(24, 31))
        invoiced = date(2026, 11, rng.randint(1, 10))
        dash(key, appointment.isoformat(), _money(amount))
        xero.append([key, invoiced.isoformat(), _money(amount), "PAID", "ACCREC"])
        claim("timing", key, amount)

    # Residual: timing and GST at once. Xero's row is outside the period AND inc GST.
    for _ in range(2):
        key, ex = number(), Decimal(rng.randint(20, 50) * 10)
        dash(key, date(2026, 10, rng.randint(25, 31)).isoformat(), _money(ex))
        xero.append(
            [
                key,
                date(2026, 11, rng.randint(2, 6)).isoformat(),
                _money(ex * (1 + GST)),
                "PAID",
                "ACCREC",
            ]
        )
        unexplained(key, "only_a", ex, "timing and GST together: amounts differ across periods")

    # Voided invoices on the dashboard only, with inconsistent spelling.
    for status, amount in [("VOIDED", 100), ("Voided", 120), ("voided ", 130), (" VOIDED", 140)]:
        key = number()
        dash(key, day().isoformat(), _money(Decimal(amount)), status)
        if status in ("VOIDED", "Voided"):
            claim("status_filter", key, Decimal(amount))
        else:
            unexplained(key, "only_a", Decimal(amount), f"status {status!r} not named in the plan")

    # Residual: dashboard-only row with no status at all.
    key = number()
    dash(key, day().isoformat(), "75.00", None)
    unexplained(key, "only_a", Decimal(75), "dashboard-only row with no status")

    # Residual: trailing space in the dashboard's invoice number, so the keys differ.
    key, when = number(), day().isoformat()
    dash(f"{key} ", when, "210.00")
    xero.append([key, when, "210.00", "PAID", "ACCREC"])
    unexplained(f"{key} ", "only_a", Decimal(210), "invoice number has a trailing space")
    unexplained(key, "only_b", Decimal(-210), "invoice number has a trailing space")

    # Residual: dashboard row with no invoice number.
    dash(None, day().isoformat(), "55.00")
    unexplained(None, "only_a", Decimal(55), "row has no invoice number")

    # Claimable: GST pairs that round correctly, including cents.
    for ex in ["33.33", "45.45", "120.00", "87.65", "12.34", "250.00"]:
        key, when, ex_amount = number(), day().isoformat(), Decimal(ex)
        inc = _cents(ex_amount * (1 + GST))
        dash(key, when, _money(ex_amount))
        xero.append([key, when, _money(inc), "PAID", "ACCREC"])
        claim("tax_basis", key, ex_amount - inc)

    # Claimable with rounding_tolerance: GST pairs 1 cent off the correctly rounded amount.
    for ex in ["33.33", "64.20"]:
        key, when, ex_amount = number(), day().isoformat(), Decimal(ex)
        inc = _cents(ex_amount * (1 + GST)) + Decimal("0.01")
        assert _cents(inc / (1 + GST)) != ex_amount, "must not be provable either way"
        dash(key, when, _money(ex_amount))
        xero.append([key, when, _money(inc), "PAID", "ACCREC"])
        claim("tax_basis", key, ex_amount - inc)

    # Claimable by the rounding check: 1 cent apart.
    key, when = number(), day().isoformat()
    dash(key, when, "100.00")
    xero.append([key, when, "100.01", "PAID", "ACCREC"])
    claim("rounding", key, Decimal("-0.01"))

    # Claimable with compare_columns: copies that differ only in load metadata.
    for _ in range(3):
        key, when, amount = number(), day().isoformat(), Decimal(rng.randint(100, 400))
        dash(key, when, _money(amount))
        dash(key, when, _money(amount), load=RELOAD)
        xero.append([key, when, _money(amount), "PAID", "ACCREC"])
        claim("duplicates", key, amount)

    # Residual: same invoice twice with the same amount but different appointment dates.
    key, amount = number(), Decimal(180)
    first = date(2026, 10, 8)
    dash(key, first.isoformat(), _money(amount))
    dash(key, (first + timedelta(days=7)).isoformat(), _money(amount))
    xero.append([key, first.isoformat(), _money(amount), "PAID", "ACCREC"])
    unexplained(key, "difference", amount, "repeated invoice on different dates, not a copy")

    # Residual: the dashboard row has no amount, so it cannot be used at all.
    key, when = number(), day().isoformat()
    dash(key, when, None)
    xero.append([key, when, "64.00", "PAID", "ACCREC"])
    unexplained(key, "data_quality", Decimal(0), "dashboard amount is empty")
    unexplained(key, "only_b", Decimal(-64), "its dashboard row has no amount")

    # Claimable: credit note with opposite signs.
    key, when, amount = "CN-901", day().isoformat(), Decimal(95)
    dash(key, when, _money(amount), kind="credit")
    xero.append([key, when, _money(-amount), "PAID", "ACCRECCREDIT"])
    claim("sign_flip", key, 2 * amount)

    rng.shuffle(dashboard)
    rng.shuffle(xero)

    claimed = sum((c["amount"] for c in claims.values()), Decimal(0))
    unexplained_total = sum((Decimal(r["amount"]) for r in residual), Decimal(0))
    summary = {
        "period": {"start": OCTOBER[0].isoformat(), "end": OCTOBER[1].isoformat()},
        "expected_gap": _money(claimed + unexplained_total),
        "plan": PLAN,
        "claims": {
            label: {"keys": c["keys"], "count": len(c["keys"]), "amount": _money(c["amount"])}
            for label, c in claims.items()
        },
        "residual": {"amount": _money(unexplained_total), "items": residual},
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / DASHBOARD_FILE, DASHBOARD_COLUMNS, dashboard)
    _write_csv(out_dir / XERO_FILE, XERO_COLUMNS, xero)
    (out_dir / MANIFEST_FILE).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return summary


def main(default_out: Path) -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default_out
    result = generate_messy(target)
    print(
        f"Wrote messy fixtures to {target} (expected gap {result['expected_gap']}, "
        f"residual {result['residual']['amount']})"
    )
