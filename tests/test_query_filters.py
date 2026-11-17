"""B2: SQL that truncates is refused; every filter becomes an assumption a person confirms."""

import pytest
from support import INVOICES, registered_sqlite, signer

from tieout_ai.connectors.base import UnsafeQueryError
from tieout_ai.connectors.sql import SQLConnector, describe_filters, validate_select
from tieout_ai.models import (
    CertificateStatus,
    MatchSpec,
    Period,
)
from tieout_ai.session import Session, Workspace

OCTOBER = Period(start="2026-10-01", end="2026-10-31")
ALL = "SELECT invoice_id, invoice_date, amount, status FROM invoices"


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("SELECT a FROM t LIMIT 5", None),
        ("SELECT TOP 5 a FROM t", "tsql"),
        ("SELECT a FROM t ORDER BY a OFFSET 0 ROWS FETCH NEXT 5 ROWS ONLY", "tsql"),
        ("SELECT a FROM t OFFSET 3", None),
        ("SELECT a FROM t TABLESAMPLE (10 PERCENT)", "tsql"),
        ("SELECT a FROM t SAMPLE 10", "snowflake"),
        ("SELECT a FROM (SELECT a FROM t LIMIT 5) AS s", None),
    ],
)
def test_queries_that_truncate_are_rejected(sql, dialect):
    with pytest.raises(UnsafeQueryError, match="would truncate the data"):
        validate_select(sql, dialect)


def filters(sql):
    return describe_filters(validate_select(sql, "sqlite"), "sqlite")


def test_a_where_clause_is_described_in_plain_english_with_its_sql():
    [only] = filters(f"{ALL} WHERE status <> 'PAID'")

    assert only.clause == "WHERE"
    assert only.sql == "status <> 'PAID'"
    assert only.plain == "status is not 'PAID'"


def test_each_and_condition_is_a_separate_filter():
    found = filters(f"{ALL} WHERE status IN ('PAID', 'OPEN') AND amount IS NOT NULL")

    assert [f.plain for f in found] == ["status is one of 'PAID', 'OPEN'", "amount is not empty"]


@pytest.mark.parametrize(
    ("sql", "clause"),
    [
        ("SELECT DISTINCT invoice_id, amount FROM invoices", "DISTINCT"),
        ("SELECT i.invoice_id, i.amount FROM invoices i JOIN paid p ON p.id = i.invoice_id", "JOIN"),  # noqa: E501
        ("SELECT status, SUM(amount) AS amount FROM invoices GROUP BY status HAVING SUM(amount) > 0", "HAVING"),  # noqa: E501
        ("SELECT invoice_id FROM invoices UNION SELECT invoice_id FROM old", "UNION"),
        ("SELECT invoice_id FROM invoices EXCEPT SELECT invoice_id FROM voided", "EXCEPT"),
        ("SELECT * FROM (SELECT * FROM invoices WHERE amount > 0) AS s", "WHERE"),
    ],
)  # fmt: skip
def test_joins_distinct_having_set_operations_and_nested_filters_are_found(sql, clause):
    assert clause in [f.clause for f in filters(sql)]


def test_union_all_and_count_distinct_are_not_filters():
    assert filters("SELECT invoice_id FROM a UNION ALL SELECT invoice_id FROM b") == []
    assert filters("SELECT COUNT(DISTINCT invoice_id) AS n FROM invoices") == []


def test_a_query_without_filters_has_none():
    assert filters(ALL) == []


@pytest.fixture
def ledger(invoices_db):
    return registered_sqlite("ledger", invoices_db)


def session_for(tmp_path, connection, query_a, query_b=None, period=None):
    a = SQLConnector(connection).fetch(query_a)
    b = SQLConnector(connection).fetch(query_b or query_a)
    spec = MatchSpec(
        extract_a=a.extract.id, extract_b=b.extract.id, key_columns=["invoice_id"],
        amount_column="amount", date_column="invoice_date" if period else None, period=period,
    )  # fmt: skip
    return Session(Workspace(tmp_path), a, b, spec)


def filter_assumptions(session):
    return [
        a
        for a in session.workspace.assumptions.get_many(session.assumption_ids)
        if a.parameter == "query.filter"
    ]


def test_a_status_filter_is_an_elevated_assumption_that_blocks_reconciled(tmp_path, ledger):
    session = session_for(tmp_path, ledger, f"{ALL} WHERE status <> 'PAID'")
    [assumption] = filter_assumptions(session)  # same query on both sides: one assumption
    first = session.certify()

    assert assumption.elevated is True
    assert "keeps only rows where status is not 'PAID'" in assumption.description
    assert "status <> 'PAID'" in assumption.description
    assert first.residual == 0
    assert first.status == CertificateStatus.PROVISIONAL
    assert assumption.id in first.unconfirmed_assumptions

    session.workspace.assumptions.confirm(assumption.id, signer())
    assert session.certify().status == CertificateStatus.RECONCILED


def test_the_extract_and_run_query_report_the_filters(tmp_path, ledger):
    from tieout_ai.service import ReconService

    session = session_for(tmp_path, ledger, f"{ALL} WHERE status <> 'PAID'")
    assert [f.sql for f in session.context.a.extract.query_filters] == ["status <> 'PAID'"]

    workspace = Workspace(tmp_path / "w")
    workspace.connections.add(ledger)
    summary = ReconService(workspace).run_query("ledger", f"{ALL} WHERE status <> 'PAID'")
    assert summary["query_filters"] == [
        {"clause": "WHERE", "sql": "status <> 'PAID'", "plain": "status is not 'PAID'"}
    ]


@pytest.mark.parametrize(
    "where",
    [
        "invoice_date BETWEEN '2026-10-01' AND '2026-10-31'",
        "invoice_date >= '2026-10-01' AND invoice_date < '2026-11-01'",
        "invoice_date >= '2026-09-01'",
    ],
)
def test_a_filter_that_only_restricts_to_the_period_is_a_normal_assumption(tmp_path, ledger, where):
    session = session_for(tmp_path, ledger, f"{ALL} WHERE {where}", period=OCTOBER)
    found = filter_assumptions(session)

    assert found
    assert not any(a.elevated for a in found)
    assert not any(a.confirmed for a in found)


@pytest.mark.parametrize(
    "where",
    [
        "invoice_date >= '2026-10-15'",
        "invoice_date BETWEEN '2026-10-01' AND '2026-10-30'",
        "amount >= 0",
    ],
)
def test_a_filter_that_cuts_into_the_period_or_elsewhere_is_elevated(tmp_path, ledger, where):
    session = session_for(tmp_path, ledger, f"{ALL} WHERE {where}", period=OCTOBER)
    [assumption] = filter_assumptions(session)

    assert assumption.elevated is True
    assert "beyond the reconciliation period" in assumption.elevated_reason


def test_a_date_filter_is_elevated_when_there_is_no_period(tmp_path, ledger):
    session = session_for(tmp_path, ledger, f"{ALL} WHERE invoice_date >= '2026-10-01'")
    [assumption] = filter_assumptions(session)

    assert assumption.elevated is True


def test_unfiltered_queries_add_no_assumptions(tmp_path, ledger):
    session = session_for(tmp_path, ledger, ALL)

    assert filter_assumptions(session) == []
    assert session.certify().status == CertificateStatus.RECONCILED
    assert len(INVOICES) == session.context.result.matched_count
