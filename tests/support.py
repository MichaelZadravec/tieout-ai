"""Shared test data and helpers."""

import json
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


FIXTURE_DATA = Path(__file__).parent.parent / "fixtures" / "data"
FIXTURE_FILES = {
    "clean": ("dashboard_oct2026.csv", "xero_oct_nov2026.csv", "manifest.json"),
    "messy": ("dashboard_messy_oct2026.csv", "xero_messy_oct_nov2026.csv", "manifest_messy.json"),
}

# The hypotheses an AI would propose for the clean fixture pair, one per injected issue.
CLEAN_PLAN = {
    "timing": ("timing", {"max_shift_days": 31}),
    "status_filter": (
        "status_filter",
        {"side": "a", "status_column": "status", "statuses": ["VOIDED"]},
    ),
    "tax_basis": ("tax_basis", {"rate": "0.10", "inclusive_side": "b"}),
    "duplicates": ("duplicates", {"side": "a"}),
    "sign_flip": ("sign_credit", {"mode": "sign_flip"}),
    "missing_credit_notes": (
        "sign_credit",
        {
            "mode": "missing_credit_notes",
            "side": "b",
            "type_column": "Type",
            "credit_types": ["ACCRECCREDIT"],
        },
    ),
}


def registered_file(conn_id: str, path: Path):
    """A file connection as `recon connections add-file` records it: hashed and signed."""
    from recon_engine.connections import file_registration
    from recon_engine.models import Connection, ConnectionType

    registered = {
        **file_registration(path),
        "registered_by": "test",
        "registered_at": "2026-11-01T00:00:00+00:00",
    }
    return signed(
        Connection(
            id=conn_id,
            type=ConnectionType.FILE,
            config={"path": str(path), "registered": registered},
        )
    )


def signed(connection):
    """A connection signed with the test confirmer key, as a person registering it would."""
    from recon_engine.connections import sign_connection

    return sign_connection(connection, signer())


def fixture_extracts(kind: str = "clean"):
    """Load a committed fixture pair: (manifest, extract A, extract B, MatchSpec)."""
    from recon_engine.connectors.files import FileConnector
    from recon_engine.models import MatchSpec

    dashboard, xero, manifest_file = FIXTURE_FILES[kind]
    manifest = json.loads((FIXTURE_DATA / manifest_file).read_text(encoding="utf-8"))

    def load(conn_id, name):
        return FileConnector(registered_file(conn_id, FIXTURE_DATA / name)).fetch()

    a, b = load("dashboard", dashboard), load("xero", xero)
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_number"],
        key_columns_b=["InvoiceNumber"],
        amount_column="amount",
        amount_column_b="Total",
        date_column="appointment_date",
        date_column_b="InvoiceDate",
        period=manifest["period"],
    )
    return manifest, a, b, spec


def fixture_context(kind: str = "clean", assumptions=None):
    from recon_engine.assumptions import AssumptionStore
    from recon_engine.checks import CheckContext
    from recon_engine.match import match

    manifest, a, b, spec = fixture_extracts(kind)
    ctx = CheckContext(
        a=a,
        b=b,
        spec=spec,
        result=match(a, b, spec),
        assumptions=assumptions if assumptions is not None else AssumptionStore(),
    )
    return manifest, ctx


def plan_for(kind: str, manifest) -> list:
    if kind == "clean":
        return list(CLEAN_PLAN.values())
    return [(name, params) for name, params in manifest["plan"]]


# match() arguments for the fixture pairs, as an AI would pass them.
FIXTURE_MATCH_ARGS = {
    "key_columns": ["invoice_number"],
    "key_columns_b": ["InvoiceNumber"],
    "amount_column": "amount",
    "amount_column_b": "Total",
    "date_column": "appointment_date",
    "date_column_b": "InvoiceDate",
    "period_start": "2026-10-01",
    "period_end": "2026-10-31",
}


def fixture_workspace(root: Path, kind: str = "clean"):
    """A workspace whose connections 'dashboard' and 'xero' point at a fixture pair."""
    from recon_engine.session import Workspace

    dashboard, xero, _ = FIXTURE_FILES[kind]
    workspace = Workspace(root)
    for conn_id, name in (("dashboard", dashboard), ("xero", xero)):
        workspace.connections.add(registered_file(conn_id, FIXTURE_DATA / name))
    return workspace


# The confirmer key every test signs with. conftest points RECON_KEYS_DIR at a
# temporary folder holding it, so tests never touch a real key.
CONFIRMER = "Jane CFO"
PASSPHRASE = "correct horse battery"


def signer():
    from recon_engine.signing import load_signer

    return load_signer(PASSPHRASE)


def registered_sqlite(conn_id: str, db: Path):
    """A SQLite connection as `recon connections add-sql` records it: hashed and signed."""
    from recon_engine.connections import file_registration
    from recon_engine.models import Connection, ConnectionType

    registered = {
        **file_registration(db, sqlite=True),
        "registered_by": "test",
        "registered_at": "2026-11-01T00:00:00+00:00",
    }
    return signed(
        Connection(
            id=conn_id,
            type=ConnectionType.SQL,
            config={"url": f"sqlite:///{db}", "registered": registered},
        )
    )
