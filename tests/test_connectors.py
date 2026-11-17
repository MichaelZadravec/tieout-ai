import sqlite3

import pandas as pd
import pytest
from support import COLUMNS, INVOICE_TOTAL, INVOICES, count_rows

from tieout_ai.connectors import connector_for
from tieout_ai.connectors import sql as sql_module
from tieout_ai.connectors.base import ConnectorError, UnsafeQueryError
from tieout_ai.connectors.files import FileConnector
from tieout_ai.connectors.sql import SQLConnector
from tieout_ai.hashing import hash_frame, hash_query
from tieout_ai.models import Connection, ConnectionType


def file_connection(path, conn_id="dashboard"):
    return Connection(id=conn_id, type=ConnectionType.FILE, config={"path": str(path)})


def sqlite_connection(path, conn_id="ledger"):
    return Connection(id=conn_id, type=ConnectionType.SQL, config={"url": f"sqlite:///{path}"})


def test_csv_extract_records_source_query_hashes_and_totals(invoices_csv):
    data = FileConnector(file_connection(invoices_csv)).fetch()
    extract = data.extract

    assert extract.connection_id == "dashboard"
    assert extract.query_text == "FILE invoices.csv"
    assert extract.query_hash == hash_query("FILE invoices.csv")
    assert extract.columns == COLUMNS
    assert extract.row_count == len(INVOICES)
    assert extract.column_totals == {"amount": INVOICE_TOTAL}
    assert extract.data_hash == hash_frame(data.frame)
    assert extract.fetched_at.tzinfo is not None


def test_refetching_the_same_file_gives_the_same_hashes(invoices_csv):
    connector = FileConnector(file_connection(invoices_csv))
    first, second = connector.fetch().extract, connector.fetch().extract

    assert first.data_hash == second.data_hash
    assert first.query_hash == second.query_hash
    assert first.id != second.id


def test_csv_empty_cells_are_missing_values(tmp_path):
    path = tmp_path / "gaps.csv"
    path.write_text("id,amount\nA,\nB,5\n", encoding="utf-8")
    data = FileConnector(file_connection(path)).fetch()

    assert data.frame["amount"].tolist() == [None, "5"]
    assert data.extract.column_totals == {"amount": 5}


def test_csv_with_excel_bom_reads_clean_headers(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text("id,amount\nA,1\n", encoding="utf-8-sig")

    assert FileConnector(file_connection(path)).fetch().extract.columns == ["id", "amount"]


def test_csv_rejects_a_sheet_name(invoices_csv):
    with pytest.raises(ConnectorError, match="CSV files have no sheets"):
        FileConnector(file_connection(invoices_csv)).fetch("Sheet1")


def test_xlsx_extract_records_the_sheet(tmp_path):
    path = tmp_path / "invoices.xlsx"
    pd.DataFrame([list(r) for r in INVOICES], columns=COLUMNS).to_excel(
        path, sheet_name="Invoices", index=False
    )
    connector = FileConnector(file_connection(path))
    default, named = connector.fetch(), connector.fetch("Invoices")

    assert default.extract.query_text == "FILE invoices.xlsx SHEET Invoices"
    assert default.extract.data_hash == named.extract.data_hash
    assert default.extract.row_count == len(INVOICES)
    assert default.extract.column_totals == {"amount": INVOICE_TOTAL}


def test_unknown_sheet_is_a_clear_error(tmp_path):
    path = tmp_path / "invoices.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(path, sheet_name="Invoices", index=False)
    with pytest.raises(ConnectorError, match="has no sheet 'Nope'"):
        FileConnector(file_connection(path)).fetch("Nope")


def test_unsupported_file_type_is_rejected(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(ConnectorError, match=r"Unsupported file type '\.txt'"):
        FileConnector(file_connection(path)).fetch()


def test_sqlite_extract_records_query_and_totals(invoices_db):
    query = "SELECT invoice_id, amount FROM invoices WHERE status = 'PAID'"
    extract = SQLConnector(sqlite_connection(invoices_db)).fetch(query).extract

    assert extract.query_text == query
    assert extract.query_hash == hash_query(query)
    assert extract.columns == ["invoice_id", "amount"]
    assert extract.row_count == 3
    assert extract.column_totals == {"amount": 1550.5}


def test_sql_rejects_non_select_before_touching_the_database(invoices_db):
    with pytest.raises(UnsafeQueryError, match="Only SELECT"):
        SQLConnector(sqlite_connection(invoices_db)).fetch("DELETE FROM invoices")
    assert count_rows(invoices_db) == len(INVOICES)


def test_sqlite_session_is_read_only_even_if_the_guard_is_bypassed(invoices_db, monkeypatch):
    monkeypatch.setattr(sql_module, "validate_select", lambda sql, dialect=None: None)
    with pytest.raises(ConnectorError, match="Query failed"):
        SQLConnector(sqlite_connection(invoices_db)).fetch("DELETE FROM invoices")
    assert count_rows(invoices_db) == len(INVOICES)


def test_sql_requires_a_query(invoices_db):
    with pytest.raises(ConnectorError, match="need a query"):
        SQLConnector(sqlite_connection(invoices_db)).fetch()


def test_sql_errors_are_reported_without_the_connection_url(tmp_path):
    db = tmp_path / "empty.sqlite"
    sqlite3.connect(db).close()
    with pytest.raises(ConnectorError, match="Query failed") as info:
        SQLConnector(sqlite_connection(db)).fetch("SELECT * FROM missing_table")
    assert str(db) not in str(info.value)


def test_credentials_never_appear_in_serialised_connections():
    conn = Connection(
        id="erp", type=ConnectionType.SQL, config={"url": "postgresql://u:s3cret@host/db"}
    )
    assert "s3cret" not in conn.model_dump_json()
    assert "s3cret" not in repr(conn)
    assert conn.config["url"].endswith("@host/db")


def test_connector_for_picks_the_connector_by_type(invoices_csv, invoices_db):
    assert isinstance(connector_for(file_connection(invoices_csv)), FileConnector)
    assert isinstance(connector_for(sqlite_connection(invoices_db)), SQLConnector)


def test_connector_rejects_a_connection_of_the_wrong_type(invoices_db):
    with pytest.raises(ConnectorError, match="expects a 'file' connection"):
        FileConnector(sqlite_connection(invoices_db))
