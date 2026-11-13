import json
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest
from support import CLEAN_PLAN, FIXTURE_MATCH_ARGS, fixture_workspace, signed

from recon_engine.connectors import UnsafeQueryError
from recon_engine.models import Connection, ConnectionType
from recon_engine.service import ReconService, ServiceError


@pytest.fixture
def service(tmp_path):
    return ReconService(fixture_workspace(tmp_path / "home"))


def matched(service):
    a = service.run_query("dashboard")["extract_id"]
    b = service.run_query("xero")["extract_id"]
    return service.match(a, b, **FIXTURE_MATCH_ARGS)


def test_list_connections_shows_ids_and_types_only(service):
    service.workspace.connections.add(
        Connection(id="erp", type=ConnectionType.SQL, config={"url": "postgresql://u:s3cret@h/db"})
    )
    listed = service.list_connections()

    assert listed == [
        {"id": "dashboard", "type": "file", "signed": True},
        {"id": "erp", "type": "sql", "signed": False},
        {"id": "xero", "type": "file", "signed": True},
    ]
    assert "s3cret" not in json.dumps(listed)


def test_run_query_returns_a_summary_not_rows(service):
    summary = service.run_query("dashboard")

    assert summary["row_count"] == 384
    assert summary["query_text"] == "FILE dashboard_oct2026.csv"
    assert len(summary["data_hash"]) == 64
    assert "rows" not in summary
    assert "path" not in json.dumps(summary).lower()


def test_unknown_connection_is_a_clear_error(service):
    with pytest.raises(ServiceError, match="No connection 'nope'"):
        service.run_query("nope")


def test_sql_queries_must_be_select_only(service, tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE t (a INT)")
    service.workspace.connections.add(
        signed(Connection(id="ledger", type=ConnectionType.SQL, config={"url": f"sqlite:///{db}"}))
    )

    with pytest.raises(UnsafeQueryError, match="Only SELECT"):
        service.run_query("ledger", "DROP TABLE t")
    assert service.run_query("ledger", "SELECT a FROM t")["row_count"] == 0


def test_profile_describes_every_column(service):
    profile = {p["column"]: p for p in service.profile_source("dashboard")["profile"]}

    assert profile["amount"]["sum"] == service.run_query("dashboard")["column_totals"]["amount"]
    assert profile["status"]["values"]["VOIDED"] == 3
    assert profile["invoice_number"]["distinct"] == 380  # 4 invoices appear twice


def test_get_rows_pages_and_looks_up_by_id(service):
    extract_id = service.run_query("dashboard")["extract_id"]
    page = service.get_rows(extract_id, offset=10, limit=5)
    by_id = service.get_rows(extract_id, row_ids=[f"{extract_id}:11"])

    assert page["total_rows"] == 384
    assert [r["row_id"] for r in page["rows"]] == [f"{extract_id}:{n}" for n in range(10, 15)]
    assert by_id["rows"] == [page["rows"][1]]
    assert set(page["rows"][0]["values"]) == {
        "invoice_number",
        "appointment_date",
        "amount",
        "status",
        "type",
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 500}, "between 1 and 200"),
        ({"limit": 0}, "between 1 and 200"),
        ({"offset": -1}, "must not be negative"),
        ({"row_ids": ["ext_other:1"]}, "is not in extract"),
    ],
)
def test_get_rows_guards_its_inputs(service, kwargs, message):
    extract_id = service.run_query("dashboard")["extract_id"]
    with pytest.raises(ServiceError, match=message):
        service.get_rows(extract_id, **kwargs)


def test_full_reconciliation_through_the_service(service):
    started = matched(service)
    rec = started["reconciliation_id"]

    assert started["gap"] == "29072.00"
    assert started["open_items"] == 61 + 2 + 19
    results = [service.run_check(rec, name, params) for name, params in CLEAN_PLAN.values()]
    assert all(r["found"] for r in results)
    assert sum(Decimal(r["amount"]) for r in results) == Decimal("29072.00")
    assert results[-1]["after_this_check"]["residual"] == "0.00"
    assert results[-1]["after_this_check"]["open_items"] == 0

    cert = service.certify(rec)
    assert cert["status"] == "PROVISIONAL"
    assert cert["residual"] == "0.00"
    assert cert["unconfirmed_assumptions"]
    assert "recon confirm" in cert["next_step"]
    assert Path(cert["html_path"]).exists()
    assert Path(cert["json_path"]).exists()

    loaded = service.get_certificate(cert["certificate_id"])
    assert loaded["hash_verified"] is True
    assert loaded["certificate_hash"] == cert["certificate_hash"]


def test_open_items_can_be_filtered_and_paged(service):
    rec = matched(service)["reconciliation_id"]
    differences = service.list_open_items(rec, kind="difference", limit=5)

    assert differences["total"] == 19
    assert len(differences["items"]) == 5
    assert all(i["kind"] == "difference" for i in differences["items"])


def test_a_wrong_hypothesis_reports_found_false_and_no_assumptions(service):
    rec = matched(service)["reconciliation_id"]
    result = service.run_check(rec, "tax_basis", {"rate": "0.15", "inclusive_side": "b"})

    assert result["found"] is False
    assert result["amount"] == "0"
    assert result["assumptions"] == []


def test_ai_proposed_assumptions_reach_the_certificate_unconfirmed(service):
    rec = matched(service)["reconciliation_id"]
    proposed = service.propose_assumption(
        rec, "scope.entity", "AU01", "Only the AU01 entity is in scope."
    )
    cert = service.certify(rec)

    assert proposed["confirmed"] is False
    assert proposed["id"] in {a["id"] for a in cert["unconfirmed_assumptions"]}


def test_recertifying_supersedes(service):
    rec = matched(service)["reconciliation_id"]
    first = service.certify(rec)
    service.run_check(rec, *CLEAN_PLAN["timing"])
    second = service.certify(rec)

    assert second["supersedes"] == first["certificate_id"]


def test_unknown_reconciliation_is_a_clear_error(service):
    with pytest.raises(ServiceError, match="No reconciliation 'rec_missing'"):
        service.run_check("rec_missing", "timing", {"max_shift_days": 5})


def test_the_service_cannot_confirm_anything():
    names = [n for n in dir(ReconService) if "confirm" in n.lower()]
    assert names == []
