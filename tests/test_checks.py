from decimal import Decimal
from typing import ClassVar

import pytest
from pydantic import BaseModel
from support import make_extract

from fixtures.generate import generate
from recon_engine.checks import (
    Check,
    CheckContext,
    CheckError,
    CheckParameterError,
    get_check,
    list_checks,
    register,
    run_checks,
)
from recon_engine.connectors.files import FileConnector
from recon_engine.match import match
from recon_engine.models import Connection, ConnectionType, MatchSpec, Period

OCTOBER = Period(start="2026-10-01", end="2026-10-31")

# The hypotheses an AI would propose for the fixture pair, one per injected issue.
PLAN = {
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


@pytest.fixture(scope="module")
def fixture_case(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixtures")
    summary = generate(out)

    def load(conn_id, name):
        conn = Connection(id=conn_id, type=ConnectionType.FILE, config={"path": str(out / name)})
        return FileConnector(conn).fetch()

    a = load("dashboard", "dashboard_oct2026.csv")
    b = load("xero", "xero_oct_nov2026.csv")
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_number"],
        key_columns_b=["InvoiceNumber"],
        amount_column="amount",
        amount_column_b="Total",
        date_column="appointment_date",
        date_column_b="InvoiceDate",
        period=OCTOBER,
    )
    return summary, CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))


@pytest.fixture
def ctx(fixture_case):
    return fixture_case[1]


@pytest.fixture
def injected(fixture_case):
    return fixture_case[0]["injected"]


def keys_of(bucket, ctx):
    return sorted({ctx.item(item_id).key[0] for item_id in bucket.item_ids})


def test_fixture_gap_matches_the_manifest(fixture_case):
    summary, ctx = fixture_case
    assert ctx.result.gap == Decimal(summary["expected_gap"])
    assert sum(item.amount for item in ctx.items) == ctx.result.gap


@pytest.mark.parametrize("issue", list(PLAN))
def test_each_check_finds_exactly_its_injected_rows(issue, ctx, injected):
    name, params = PLAN[issue]
    bucket = get_check(name).run(ctx, params)

    assert keys_of(bucket, ctx) == sorted(injected[issue]["keys"])
    assert bucket.amount == Decimal(injected[issue]["amount"])
    assert bucket.row_count == len(bucket.row_ids)
    assert bucket.check_name == name
    assert bucket.check_version


def test_all_five_checks_together_explain_the_whole_gap(ctx):
    buckets = run_checks(ctx, list(PLAN.values()))

    assert sum(b.amount for b in buckets) == ctx.result.gap
    assert {get_check(name).name for name, _ in PLAN.values()} == {
        "timing",
        "status_filter",
        "tax_basis",
        "duplicates",
        "sign_credit",
    }


def test_no_row_or_item_lands_in_two_buckets(ctx):
    buckets = run_checks(ctx, list(PLAN.values()))
    rows = [r for b in buckets for r in b.row_ids]
    items = [i for b in buckets for i in b.item_ids]

    assert len(rows) == len(set(rows))
    assert len(items) == len(set(items))


def test_a_check_never_reclaims_rows_already_claimed(ctx):
    plan = [PLAN["status_filter"], PLAN["status_filter"]]
    first, second = run_checks(ctx, plan)

    assert first.row_count == 3
    assert second.row_ids == []
    assert second.amount == 0


def test_timing_evidence_is_the_counterpart_row_outside_the_period(ctx):
    bucket = get_check("timing").run(ctx, PLAN["timing"][1])

    assert len(bucket.evidence_row_ids) == 58
    assert set(bucket.evidence_row_ids) <= set(ctx.result.excluded_b)


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("timing", {"max_shift_days": 2}),
        ("status_filter", {"side": "a", "status_column": "status", "statuses": ["DRAFT"]}),
        ("status_filter", {"side": "b", "status_column": "Status", "statuses": ["VOIDED"]}),
        ("tax_basis", {"rate": "0.15", "inclusive_side": "b"}),
        ("tax_basis", {"rate": "0.10", "inclusive_side": "a"}),
        ("duplicates", {"side": "b"}),
        (
            "sign_credit",
            {
                "mode": "missing_credit_notes",
                "side": "a",
                "type_column": "type",
                "credit_types": ["credit"],
            },
        ),
    ],
)
def test_wrong_hypotheses_return_empty_buckets(name, params, ctx):
    bucket = get_check(name).run(ctx, params)

    assert bucket.row_ids == []
    assert bucket.item_ids == []
    assert bucket.amount == 0
    assert bucket.row_count == 0


@pytest.mark.parametrize(
    ("name", "params", "message"),
    [
        ("timing", {"max_shift_days": 32}, "at most one period"),
        ("timing", {"max_shift_days": 0}, "greater than or equal to 1"),
        ("status_filter", {"side": "a", "status_column": "status", "statuses": []}, "at least 1"),
        (
            "status_filter",
            {"side": "a", "status_column": "nope", "statuses": ["VOIDED"]},
            "has no column 'nope'",
        ),
        ("tax_basis", {"rate": "0.5", "inclusive_side": "b"}, "less than or equal to 0.25"),
        ("tax_basis", {"rate": "0", "inclusive_side": "b"}, "greater than 0"),
        ("duplicates", {"side": "c"}, "'a' or 'b'"),
        ("duplicates", {"side": "a", "loose": True}, "Extra inputs are not permitted"),
        ("sign_credit", {"mode": "guess"}, "sign_flip"),
        (
            "sign_credit",
            {"mode": "missing_credit_notes", "side": "b"},
            "type_column and credit_types",
        ),
    ],
)
def test_guardrails_reject_out_of_range_parameters(name, params, message, ctx):
    with pytest.raises(CheckParameterError, match=message):
        get_check(name).run(ctx, params)


def test_timing_needs_a_period():
    a = make_extract([("INV-1", "2026-10-03", "1", "PAID")], "a")
    b = make_extract([("INV-1", "2026-11-03", "1", "PAID")], "b")
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_id"],
        amount_column="amount",
    )
    ctx = CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))
    with pytest.raises(CheckParameterError, match="needs a MatchSpec period"):
        get_check("timing").run(ctx, {"max_shift_days": 5})


def test_timing_works_in_both_directions():
    # B dated in October, A dated in late September: the row is only on B for October.
    a = make_extract([("INV-1", "2026-09-28", "80.00", "PAID")], "a")
    b = make_extract([("INV-1", "2026-10-02", "80.00", "PAID")], "b")
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_id"],
        amount_column="amount",
        date_column="invoice_date",
        period=OCTOBER,
    )
    ctx = CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))
    bucket = get_check("timing").run(ctx, {"max_shift_days": 7})

    assert ctx.result.gap == Decimal("-80.00")
    assert bucket.amount == Decimal("-80.00")
    assert bucket.evidence_row_ids == [f"{a.extract.id}:0"]


def test_timing_requires_the_counterpart_amount_to_agree():
    a = make_extract([("INV-1", "2026-10-30", "80.00", "PAID")], "a")
    b = make_extract([("INV-1", "2026-11-02", "79.00", "PAID")], "b")
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_id"],
        amount_column="amount",
        date_column="invoice_date",
        period=OCTOBER,
    )
    ctx = CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))

    assert get_check("timing").run(ctx, {"max_shift_days": 7}).row_ids == []


def test_buckets_record_parameters_and_assumptions(ctx):
    bucket = get_check("status_filter").run(ctx, PLAN["status_filter"][1])

    assert bucket.parameters == {"side": "a", "status_column": "status", "statuses": ["VOIDED"]}
    [assumption] = bucket.assumptions
    assert assumption.parameter == "status_filter.statuses"
    assert assumption.value == ["VOIDED"]
    assert assumption.proposed_by == "ai"
    assert assumption.confirmed is False
    assert assumption.confirmed_by is None


def test_assumption_ids_are_deterministic(ctx):
    first = get_check("tax_basis").run(ctx, PLAN["tax_basis"][1]).assumptions
    second = get_check("tax_basis").run(ctx, PLAN["tax_basis"][1]).assumptions

    assert [a.id for a in first] == [a.id for a in second]


def test_list_checks_describes_every_check():
    listed = {c.name: c for c in list_checks()}

    assert set(listed) == {"timing", "status_filter", "tax_basis", "duplicates", "sign_credit"}
    for check in listed.values():
        assert check.version
        assert check.description
        assert check.Params.model_json_schema()["properties"]


def test_unknown_check_is_a_clear_error():
    with pytest.raises(CheckError, match="No check named 'magic'"):
        get_check("magic")


def test_the_engine_refuses_a_check_that_claims_rows_twice(ctx):
    class Greedy(Check):
        name: ClassVar[str] = "greedy_test_only"
        version: ClassVar[str] = "0.0.0"
        description: ClassVar[str] = "Claims everything, twice."

        class Params(BaseModel):
            pass

        def prove(self, ctx, items, params):
            return [*items, *items]

    with pytest.raises(CheckError, match="claimed item"):
        Greedy().run(ctx, {})


def test_register_rejects_duplicate_names():
    with pytest.raises(CheckError, match="already registered"):

        @register
        class Again(Check):
            name: ClassVar[str] = "timing"
            version: ClassVar[str] = "9.9.9"
            description: ClassVar[str] = "dup"

            class Params(BaseModel):
                pass

            def prove(self, ctx, items, params):
                return []
