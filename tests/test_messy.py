from decimal import Decimal
from pathlib import Path

import pytest

from fixtures.generate_messy import DASHBOARD_FILE, MANIFEST_FILE, XERO_FILE, generate_messy
from recon_engine.checks import CheckContext, CheckParameterError, get_check, run_checks
from recon_engine.connectors.files import FileConnector
from recon_engine.match import match
from recon_engine.models import Connection, ConnectionType, MatchSpec, Period

COMMITTED = Path(__file__).parent.parent / "fixtures" / "data"
LABELS = ["timing", "status_filter", "tax_basis", "duplicates", "sign_flip"]


@pytest.fixture(scope="module")
def messy(tmp_path_factory):
    out = tmp_path_factory.mktemp("messy")
    summary = generate_messy(out)

    def load(conn_id, name):
        conn = Connection(id=conn_id, type=ConnectionType.FILE, config={"path": str(out / name)})
        return FileConnector(conn).fetch()

    a, b = load("dashboard", DASHBOARD_FILE), load("xero", XERO_FILE)
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_number"],
        key_columns_b=["InvoiceNumber"],
        amount_column="amount",
        amount_column_b="Total",
        date_column="appointment_date",
        date_column_b="InvoiceDate",
        period=Period.model_validate(summary["period"]),
    )
    return summary, CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))


def plan_of(summary):
    return [(name, params) for name, params in summary["plan"]]


def keys_of(bucket, ctx):
    return sorted(ctx.item(i).key[0] for i in bucket.item_ids)


def test_messy_generator_is_deterministic_and_matches_the_committed_files(tmp_path):
    generate_messy(tmp_path)
    for name in (DASHBOARD_FILE, XERO_FILE, MANIFEST_FILE):
        assert (tmp_path / name).read_bytes() == (COMMITTED / name).read_bytes(), name


def test_messy_gap_matches_the_manifest(messy):
    summary, ctx = messy
    assert ctx.result.gap == Decimal(summary["expected_gap"])


def test_each_check_claims_exactly_the_rows_it_can_prove(messy):
    summary, ctx = messy
    buckets = run_checks(ctx, plan_of(summary))

    for label, bucket in zip(LABELS, buckets, strict=True):
        expected = summary["claims"][label]
        assert keys_of(bucket, ctx) == sorted(expected["keys"]), label
        assert bucket.amount == Decimal(expected["amount"]), label


def test_honest_residual_is_exactly_the_unprovable_items(messy):
    summary, ctx = messy
    buckets = run_checks(ctx, plan_of(summary))
    claimed = {i for b in buckets for i in b.item_ids}
    left = sorted(
        ((i.key[0] or "", i.kind, i.amount) for i in ctx.items if i.id not in claimed),
    )
    expected = sorted(
        (r["key"] or "", r["kind"], Decimal(r["amount"])) for r in summary["residual"]["items"]
    )

    assert left == expected
    residual = ctx.result.gap - sum(b.amount for b in buckets)
    assert residual == Decimal(summary["residual"]["amount"])
    assert residual != 0


def test_no_row_is_claimed_twice_on_the_messy_pair(messy):
    summary, ctx = messy
    rows = [r for b in run_checks(ctx, plan_of(summary)) for r in b.row_ids]

    assert len(rows) == len(set(rows))


def test_status_matching_is_exact_so_case_and_whitespace_variants_stay_open(messy):
    _, ctx = messy
    bucket = get_check("status_filter").run(
        ctx, {"side": "a", "status_column": "status", "statuses": ["VOIDED"]}
    )
    assert bucket.amount == Decimal(100)


def test_near_duplicates_need_compare_columns(messy):
    _, ctx = messy
    assert get_check("duplicates").run(ctx, {"side": "a"}).item_ids == []


def test_compare_columns_cannot_drop_the_date_column(messy):
    """INV-20191 is one invoice on two dates: not a copy. Dropping the date would claim it."""
    _, ctx = messy
    without_date = ["invoice_number", "amount", "status", "type"]

    with pytest.raises(CheckParameterError) as rejected:
        get_check("duplicates").run(ctx, {"side": "a", "compare_columns": without_date})

    assert str(rejected.value) == (
        "compare_columns must keep the match's core columns (key, amount and dates); "
        "missing: 'appointment_date'"
    )


def test_the_repeated_invoice_on_two_dates_is_never_a_duplicate(messy):
    _, ctx = messy
    every_allowed = ["invoice_number", "appointment_date", "amount"]
    bucket = get_check("duplicates").run(ctx, {"side": "a", "compare_columns": every_allowed})

    assert "INV-20191" not in keys_of(bucket, ctx)


def test_compound_timing_and_gst_is_claimed_by_neither_check(messy):
    summary, ctx = messy
    compound = {
        r["key"] for r in summary["residual"]["items"] if r["reason"].startswith("timing and GST")
    }
    timing = get_check("timing").run(ctx, {"max_shift_days": 31})
    tax = get_check("tax_basis").run(ctx, {"rate": "0.10", "inclusive_side": "b"})

    assert len(compound) == 2
    assert compound.isdisjoint(keys_of(timing, ctx))
    assert compound.isdisjoint(keys_of(tax, ctx))


def test_padded_amounts_and_lower_case_statuses_still_match(messy):
    _, ctx = messy
    assert ctx.result.matched_count >= 150
