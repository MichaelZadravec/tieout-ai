"""Hardening (3): computed SQL columns are elevated assumptions; SQLite files are hashed.

Red-team attack 12 rewrote an amount with CASE (not a filter, so nothing was
recorded) and attack 13 edited the SQLite database directly (SQL sources had no
registration hash).
"""

import sqlite3
from contextlib import closing

import pytest
from support import CONFIRMER, INVOICES, PASSPHRASE, registered_sqlite, signed
from typer.testing import CliRunner

from tieout_ai import cli
from tieout_ai.cli import app
from tieout_ai.connectors.sql import SQLConnector, computed_columns, validate_select
from tieout_ai.models import CertificateStatus, Connection, ConnectionType, MatchSpec
from tieout_ai.service import ReconService
from tieout_ai.session import Session, Workspace

CODE = "K7Q2PX"
ALL = "SELECT invoice_id, invoice_date, amount, status FROM invoices"


def computed(sql, dialect="sqlite"):
    return computed_columns(validate_select(sql, dialect), dialect)


def test_a_case_expression_is_a_computed_column():
    found = computed(
        "SELECT invoice_id, CASE WHEN invoice_id = 'INV-003' THEN 0 ELSE amount END AS amount "
        "FROM invoices"
    )
    assert found == {"amount": "CASE WHEN invoice_id = 'INV-003' THEN 0 ELSE amount END"}


def test_renames_are_not_computed():
    assert computed("SELECT invoice_id AS InvoiceNumber, amount AS Total FROM invoices") == {}
    assert computed(ALL) == {}
    assert computed("SELECT * FROM invoices") == {}


def test_a_computed_value_renamed_in_an_outer_query_is_traced():
    found = computed(
        "SELECT invoice_id, t AS amount FROM (SELECT invoice_id, amount * 2 AS t FROM invoices) s"
    )
    assert found == {"amount": "amount * 2"}


def test_aggregates_are_computed():
    found = computed("SELECT invoice_id, SUM(amount) AS amount FROM invoices GROUP BY invoice_id")
    assert found == {"amount": "SUM(amount)"}


@pytest.fixture
def ledger(invoices_db):
    return registered_sqlite("ledger", invoices_db)


@pytest.fixture
def unhashed_ledger(invoices_db):
    return signed(
        Connection(id="ledger", type=ConnectionType.SQL, config={"url": f"sqlite:///{invoices_db}"})
    )


def session_for(tmp_path, connection, query):
    a = SQLConnector(connection).fetch(ALL)
    b = SQLConnector(connection).fetch(query)
    spec = MatchSpec(
        extract_a=a.extract.id, extract_b=b.extract.id, key_columns=["invoice_id"],
        amount_column="amount",
    )  # fmt: skip
    return Session(Workspace(tmp_path), a, b, spec)


def computed_assumptions(session):
    return [
        a
        for a in session.workspace.assumptions.get_many(session.assumption_ids)
        if a.parameter == "query.computed_column"
    ]


def test_a_rewritten_amount_is_an_elevated_assumption_that_blocks_reconciled(tmp_path, ledger):
    """Attack 12."""
    rewrite = (
        "SELECT invoice_id, invoice_date, CASE WHEN invoice_id = 'INV-003' THEN 75.25 "
        "ELSE amount END AS amount, status FROM invoices"
    )
    session = session_for(tmp_path, ledger, rewrite)
    [assumption] = computed_assumptions(session)
    cert = session.certify()

    assert assumption.elevated is True
    assert "computes its amount column 'amount'" in assumption.description
    assert "CASE WHEN" in assumption.description
    assert cert.status == CertificateStatus.PROVISIONAL
    assert assumption.id in cert.unconfirmed_assumptions


def test_a_computed_status_column_is_elevated_too(tmp_path, ledger):
    rewrite = (
        "SELECT invoice_id, invoice_date, amount, CASE WHEN amount > 1000 THEN 'VOIDED' "
        "ELSE status END AS status FROM invoices"
    )
    [assumption] = computed_assumptions(session_for(tmp_path, ledger, rewrite))

    assert assumption.elevated is True
    assert "computes its column 'status'" in assumption.description


def test_plain_queries_add_no_computed_assumptions(tmp_path, ledger):
    assert computed_assumptions(session_for(tmp_path, ledger, ALL)) == []


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_confirmation_code", lambda: CODE)


def recon(home, *args, input=None):
    return CliRunner().invoke(app, ["--home", str(home), *args], input=input)


@pytest.fixture
def registered_db(tmp_path, invoices_db, interactive):
    home = tmp_path / ".recon"
    url = f"sqlite:///{invoices_db}"
    added = recon(home, "connections", "add-sql", "ledger", "--url", url, input=f"{PASSPHRASE}\n")
    assert added.exit_code == 0, added.output
    return home, invoices_db


def test_add_sql_hashes_a_sqlite_file(registered_db):
    home, _ = registered_db
    registered = Workspace(home).connections.get("ledger").config["registered"]

    assert len(registered["sha256"]) == 64
    assert registered["registered_by"] == CONFIRMER
    assert ReconService(Workspace(home)).run_query("ledger", ALL)["source_warning"] is None


def test_editing_the_sqlite_file_is_caught(registered_db):
    """Attack 13."""
    home, db = registered_db
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("UPDATE invoices SET amount = 999 WHERE invoice_id = 'INV-001'")

    extract = ReconService(Workspace(home)).run_query("ledger", ALL)

    assert extract["changed_since_registration"] is True
    assert "ledger.sqlite" in extract["source_warning"]
    assert "changed since it was registered" in extract["source_warning"]


def test_refresh_re_registers_a_sqlite_file(registered_db):
    home, db = registered_db
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("UPDATE invoices SET amount = 999 WHERE invoice_id = 'INV-001'")

    refreshed = recon(home, "connections", "refresh", "ledger", input=f"{CODE}\n{PASSPHRASE}\n")

    assert refreshed.exit_code == 0, refreshed.output
    assert ReconService(Workspace(home)).run_query("ledger", ALL)["source_warning"] is None


def test_a_sqlite_connection_without_a_hash_is_flagged(tmp_path, unhashed_ledger):
    data = SQLConnector(unhashed_ledger).fetch(ALL)

    assert "not registered with a file hash" in data.extract.source_warning
    assert len(INVOICES) == data.extract.row_count


def test_a_server_database_has_no_file_to_hash(tmp_path):
    from tieout_ai.connectors.sql import sqlite_file

    assert sqlite_file("postgresql://reader@db.example/finance") is None
    assert sqlite_file("sqlite:///:memory:") is None
    assert sqlite_file(f"sqlite:///{tmp_path / 'x.sqlite'}") == tmp_path / "x.sqlite"


def test_run_query_reports_computed_columns(tmp_path, ledger):
    workspace = Workspace(tmp_path)
    workspace.connections.add(ledger)
    summary = ReconService(workspace).run_query(
        "ledger", "SELECT invoice_id, amount * 2 AS amount FROM invoices"
    )

    assert summary["computed_columns"] == {"amount": "amount * 2"}
