"""Shared test data and helpers."""

import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pandas as pd

from recon_engine.connectors.base import build_extract
from recon_engine.models import ExtractData

COLUMNS = ["invoice_id", "invoice_date", "amount", "status"]
INVOICES = [
    ("INV-001", "2026-10-03", "100.00", "PAID"),
    ("INV-002", "2026-10-07", "250.50", "PAID"),
    ("INV-003", "2026-10-15", "75.25", "VOIDED"),
    ("INV-004", "2026-10-31", "1200.00", "PAID"),
    ("INV-005", "2026-11-01", "60.00", "DRAFT"),
]
INVOICE_TOTAL = sum(Decimal(row[2]) for row in INVOICES)


def make_extract(rows, connection_id="test", columns=COLUMNS) -> ExtractData:
    frame = pd.DataFrame([list(r) for r in rows], columns=list(columns), dtype=object)
    return build_extract(connection_id, f"TEST {connection_id}", frame)


def count_rows(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as db:
        return db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
