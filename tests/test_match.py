from decimal import Decimal

import pytest
from pydantic import ValidationError
from support import INVOICE_TOTAL, INVOICES, make_extract

from recon_engine.connectors.files import FileConnector
from recon_engine.connectors.sql import SQLConnector
from recon_engine.match import MatchError, match
from recon_engine.models import Connection, ConnectionType, MatchSpec, Period

OCTOBER = Period(start="2026-10-01", end="2026-10-31")


def spec_for(a, b, **overrides):
    fields = {"key_columns": ["invoice_id"], "amount_column": "amount", **overrides}
    return MatchSpec(extract_a=a.extract.id, extract_b=b.extract.id, **fields)


def assert_conserved(result):
    explained = (
        sum((r.amount for r in result.only_a), Decimal(0))
        - sum((r.amount for r in result.only_b), Decimal(0))
        + sum((d.difference for d in result.amount_differences), Decimal(0))
    )
    assert result.gap == explained
    assert result.gap == result.total_a - result.total_b


def test_identical_pair_reconciles_with_zero_gap():
    a, b = make_extract(INVOICES, "a"), make_extract(INVOICES, "b")
    result = match(a, b, spec_for(a, b))

    assert result.gap == 0
    assert result.matched_count == len(INVOICES)
    assert result.only_a == []
    assert result.only_b == []
    assert result.amount_differences == []
    assert result.total_a == result.total_b == INVOICE_TOTAL


def test_identical_csv_and_sqlite_sources_reconcile_with_zero_gap(invoices_csv, invoices_db):
    a = FileConnector(
        Connection(id="dash", type=ConnectionType.FILE, config={"path": str(invoices_csv)})
    ).fetch()
    b = SQLConnector(
        Connection(id="ledger", type=ConnectionType.SQL, config={"url": f"sqlite:///{invoices_db}"})
    ).fetch("SELECT invoice_id, invoice_date, amount, status FROM invoices")
    result = match(a, b, spec_for(a, b))

    assert result.gap == 0
    assert result.matched_count == len(INVOICES)


def test_rows_on_one_side_only_are_reported_with_their_amounts():
    a = make_extract(INVOICES, "a")
    b = make_extract([*INVOICES[1:], ("INV-999", "2026-10-20", "40.00", "PAID")], "b")
    result = match(a, b, spec_for(a, b))

    assert [(r.row_id, r.key, r.amount) for r in result.only_a] == [
        (f"{a.extract.id}:0", ["INV-001"], Decimal("100.00"))
    ]
    assert [(r.row_id, r.key, r.amount) for r in result.only_b] == [
        (f"{b.extract.id}:4", ["INV-999"], Decimal("40.00"))
    ]
    assert result.gap == Decimal("60.00")
    assert result.matched_count == len(INVOICES) - 1
    assert_conserved(result)


def test_amount_differences_are_reported_per_key():
    changed = [INVOICES[0], ("INV-002", "2026-10-07", "230.50", "PAID"), *INVOICES[2:]]
    a, b = make_extract(INVOICES, "a"), make_extract(changed, "b")
    result = match(a, b, spec_for(a, b))

    [diff] = result.amount_differences
    assert diff.key == ["INV-002"]
    assert (diff.amount_a, diff.amount_b, diff.difference) == (
        Decimal("250.50"),
        Decimal("230.50"),
        Decimal("20.00"),
    )
    assert diff.row_ids_a == [f"{a.extract.id}:1"]
    assert diff.row_ids_b == [f"{b.extract.id}:1"]
    assert result.gap == Decimal("20.00")
    assert_conserved(result)


def test_gap_is_conserved_across_every_kind_of_difference():
    a = make_extract(INVOICES, "a")
    b = make_extract(
        [
            ("INV-002", "2026-10-07", "250.00", "PAID"),
            ("INV-003", "2026-10-15", "75.25", "VOIDED"),
            ("INV-004", "2026-10-31", "1300.00", "PAID"),
            ("INV-777", "2026-10-12", "15.10", "PAID"),
        ],
        "b",
    )
    result = match(a, b, spec_for(a, b))

    assert len(result.only_a) == 2
    assert len(result.only_b) == 1
    assert len(result.amount_differences) == 2
    assert_conserved(result)


def test_duplicate_keys_are_summed_and_every_row_is_listed():
    a = make_extract([INVOICES[0], INVOICES[0]], "a")
    b = make_extract([INVOICES[0]], "b")
    result = match(a, b, spec_for(a, b))

    [diff] = result.amount_differences
    assert diff.row_ids_a == [f"{a.extract.id}:0", f"{a.extract.id}:1"]
    assert diff.difference == Decimal("100.00")
    assert result.matched_count == 0


def test_composite_keys_match_on_every_column():
    a = make_extract(INVOICES, "a")
    b = make_extract([("INV-001", "2026-10-04", "100.00", "PAID"), *INVOICES[1:]], "b")
    result = match(a, b, spec_for(a, b, key_columns=["invoice_id", "invoice_date"]))

    assert [r.key for r in result.only_a] == [["INV-001", "2026-10-03"]]
    assert [r.key for r in result.only_b] == [["INV-001", "2026-10-04"]]
    assert result.gap == 0


def test_side_b_can_use_different_column_names():
    a = make_extract(INVOICES, "a")
    b = make_extract(INVOICES, "b", columns=["InvoiceNumber", "Date", "Total", "Status"])
    result = match(a, b, spec_for(a, b, key_columns_b=["InvoiceNumber"], amount_column_b="Total"))

    assert result.gap == 0
    assert result.matched_count == len(INVOICES)


def test_keys_match_across_source_types():
    a = make_extract([("1001", "2026-10-03", "10", "PAID")], "a")
    b = make_extract([(1001, "2026-10-03", 10.0, "PAID")], "b")

    assert match(a, b, spec_for(a, b)).matched_count == 1


def test_rows_with_a_missing_key_never_match():
    a = make_extract([(None, "2026-10-03", "5", "PAID")], "a")
    b = make_extract([(None, "2026-10-03", "5", "PAID")], "b")
    result = match(a, b, spec_for(a, b))

    assert result.matched_count == 0
    assert [r.key for r in result.only_a] == [[None]]
    assert [r.key for r in result.only_b] == [[None]]
    assert result.gap == 0


def test_missing_column_is_a_clear_error():
    a, b = make_extract(INVOICES, "a"), make_extract(INVOICES, "b")
    with pytest.raises(MatchError, match=r"side B .* has no column 'total'"):
        match(a, b, spec_for(a, b, amount_column_b="total"))


def test_non_numeric_amount_becomes_a_data_quality_row_not_an_error():
    a = make_extract([("INV-001", "2026-10-03", "ten", "PAID"), *INVOICES[1:]], "a")
    b = make_extract(INVOICES, "b")
    result = match(a, b, spec_for(a, b))

    [invalid] = result.invalid_a
    assert invalid.key == ["INV-001"]
    assert "'ten' is not a number" in invalid.reason
    assert [r.key for r in result.only_b] == [["INV-001"]]
    assert result.gap == Decimal("-100.00")
    assert_conserved(result)


def test_empty_amount_becomes_a_data_quality_row_not_an_error():
    a = make_extract([("INV-001", "2026-10-03", None, "PAID"), *INVOICES[1:]], "a")
    b = make_extract(INVOICES, "b")
    result = match(a, b, spec_for(a, b))

    [invalid] = result.invalid_a
    assert invalid.row_id == f"{a.extract.id}:0"
    assert invalid.key == ["INV-001"]
    assert "amount" in invalid.reason
    assert result.invalid_b == []
    # The row is left out of the totals, so B's INV-001 is unmatched.
    assert [r.key for r in result.only_b] == [["INV-001"]]
    assert result.total_a == INVOICE_TOTAL - Decimal("100.00")
    assert result.gap == Decimal("-100.00")
    assert_conserved(result)


def test_period_filter_excludes_and_records_rows_outside_the_period():
    a, b = make_extract(INVOICES, "a"), make_extract(INVOICES[:4], "b")
    result = match(a, b, spec_for(a, b, date_column="invoice_date", period=OCTOBER))

    assert result.excluded_a == [f"{a.extract.id}:4"]
    assert result.excluded_b == []
    assert result.gap == 0
    assert result.total_a == INVOICE_TOTAL - Decimal("60.00")


def test_unparseable_date_is_a_clear_error():
    a = make_extract([("INV-001", "03/10/2026", "100.00", "PAID")], "a")
    b = make_extract(INVOICES, "b")
    with pytest.raises(MatchError, match="is not an ISO date"):
        match(a, b, spec_for(a, b, date_column="invoice_date", period=OCTOBER))


def test_period_requires_a_date_column():
    with pytest.raises(ValidationError, match="period requires date_column"):
        MatchSpec(
            extract_a="a",
            extract_b="b",
            key_columns=["invoice_id"],
            amount_column="amount",
            period=OCTOBER,
        )


def test_period_end_cannot_precede_start():
    with pytest.raises(ValidationError, match="end must not be before start"):
        Period(start="2026-10-31", end="2026-10-01")


def test_key_column_counts_must_agree():
    with pytest.raises(ValidationError, match="same number of key columns"):
        MatchSpec(
            extract_a="a",
            extract_b="b",
            key_columns=["invoice_id"],
            key_columns_b=["InvoiceNumber", "Date"],
            amount_column="amount",
        )


def test_extracts_must_be_the_ones_named_in_the_spec():
    a, b = make_extract(INVOICES, "a"), make_extract(INVOICES, "b")
    with pytest.raises(MatchError, match="do not match the MatchSpec"):
        match(b, a, spec_for(a, b))
