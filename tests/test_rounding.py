"""Rounding tolerance: the rounding check, and tax_basis's optional rounding_tolerance."""

from decimal import Decimal

import pytest
from support import fixture_context, make_extract

from tieout_ai.checks import CheckContext, CheckParameterError, get_check, run_checks
from tieout_ai.match import match
from tieout_ai.models import MatchSpec

ROUNDING_KEYS = ["INV-20185", "INV-20186", "INV-20187"]
TAX = {"rate": "0.10", "inclusive_side": "b"}


def keys_of(ctx, bucket):
    return sorted(ctx.item(i).key[0] for i in bucket.item_ids)


def pair(amount_a, amount_b):
    a = make_extract([("INV-1", "2026-10-03", amount_a, "PAID")], "dashboard")
    b = make_extract([("INV-1", "2026-10-03", amount_b, "PAID")], "xero")
    spec = MatchSpec(
        extract_a=a.extract.id, extract_b=b.extract.id, key_columns=["invoice_id"],
        amount_column="amount",
    )  # fmt: skip
    return CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))


def test_rounding_claims_differences_within_the_default_cent():
    ctx = pair("100.00", "100.01")
    bucket = get_check("rounding").run(ctx, {})

    assert bucket.parameters == {"tolerance": "0.01"}
    assert bucket.amount == Decimal("-0.01")


def test_rounding_leaves_larger_differences_alone():
    assert get_check("rounding").run(pair("100.00", "100.02"), {}).item_ids == []


def test_rounding_tolerance_is_always_an_elevated_assumption():
    ctx = pair("100.00", "100.01")
    bucket = get_check("rounding").run(ctx, {})
    [assumption] = ctx.assumptions.get_many(bucket.assumption_ids)

    assert assumption.elevated is True
    assert assumption.elevated_reason == (
        "loosens exact matching: accepts differences up to $0.01 per record as rounding"
    )
    assert assumption.description.startswith("For this record (INV-1), dashboard and xero")


@pytest.mark.parametrize("tolerance", ["0.06", "1", "0", "-0.01"])
def test_rounding_tolerance_outside_the_limits_is_rejected(tolerance):
    with pytest.raises(
        CheckParameterError, match=r"tolerance must be above \$0 and at most \$0.05"
    ):
        get_check("rounding").run(pair("100.00", "100.01"), {"tolerance": tolerance})


def test_rounding_only_claims_amount_differences_on_matched_keys():
    _, ctx = fixture_context("messy")
    bucket = get_check("rounding").run(ctx, {"tolerance": "0.05"})

    assert all(ctx.item(i).kind == "difference" for i in bucket.item_ids)
    assert keys_of(ctx, bucket) == ["INV-20187"]


def test_tax_basis_with_a_cent_of_tolerance_claims_the_off_by_a_cent_pairs():
    _, ctx = fixture_context("messy")
    exact = get_check("tax_basis").run(ctx, TAX)
    tolerant = get_check("tax_basis").run(ctx, {**TAX, "rounding_tolerance": "0.01"})

    assert set(keys_of(ctx, tolerant)) - set(keys_of(ctx, exact)) == {"INV-20185", "INV-20186"}


def test_tax_basis_tolerance_is_elevated_and_exact_is_not():
    _, ctx = fixture_context("messy")
    exact = get_check("tax_basis").run(ctx, TAX)
    tolerant = get_check("tax_basis").run(ctx, {**TAX, "rounding_tolerance": "0.01"})
    [plain] = ctx.assumptions.get_many(exact.assumption_ids)
    [loose] = ctx.assumptions.get_many(tolerant.assumption_ids)

    assert exact.parameters["rounding_tolerance"] == "0"
    assert plain.elevated is False
    assert loose.elevated is True
    assert loose.elevated_reason == (
        "loosens the default: accepts tax amounts up to $0.01 off the rounded value"
    )


@pytest.mark.parametrize("tolerance", ["0.02", "-0.01"])
def test_tax_basis_tolerance_over_a_cent_is_rejected(tolerance):
    _, ctx = fixture_context("messy")
    with pytest.raises(
        CheckParameterError, match=r"rounding_tolerance must be between \$0 and \$0.01"
    ):
        get_check("tax_basis").run(ctx, {**TAX, "rounding_tolerance": tolerance})


def test_messy_rounding_items_are_claimed_and_the_residual_moves_by_exactly_978():
    manifest, ctx = fixture_context("messy")
    plan = [(name, params) for name, params in manifest["plan"]]
    without = [
        (name, {k: v for k, v in params.items() if k != "rounding_tolerance"})
        for name, params in plan
        if name != "rounding"
    ]
    before = ctx.result.gap - sum(b.amount for b in run_checks(ctx, without))
    buckets = run_checks(ctx, plan)
    after = ctx.result.gap - sum(b.amount for b in buckets)
    claimed = {ctx.item(i).key[0] for b in buckets for i in b.item_ids}

    assert set(ROUNDING_KEYS) <= claimed
    # The three items are A - B = -3.34, -6.43 and -0.01: once explained, the
    # unexplained remainder no longer carries them, a move of exactly $9.78.
    assert after - before == Decimal("9.78")
    assert sum(abs(i.amount) for i in ctx.items if i.key[0] in ROUNDING_KEYS) == Decimal("9.78")


def test_no_record_is_claimed_by_both_tax_basis_and_rounding():
    _, ctx = fixture_context("messy")
    for order in (
        [("rounding", {"tolerance": "0.05"}), ("tax_basis", {**TAX, "rounding_tolerance": "0.01"})],
        [("tax_basis", {**TAX, "rounding_tolerance": "0.01"}), ("rounding", {"tolerance": "0.05"})],
    ):
        first, second = run_checks(ctx, order)
        assert set(first.item_ids).isdisjoint(second.item_ids)
        assert set(first.row_ids).isdisjoint(second.row_ids)
