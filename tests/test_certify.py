from datetime import UTC, datetime
from decimal import Decimal

import pytest
from support import CLEAN_PLAN, fixture_context, make_extract, plan_for

from recon_engine import __version__
from recon_engine.assumptions import AssumptionStore
from recon_engine.certify import ConservationError, certify, verify_certificate
from recon_engine.checks import CheckContext, get_check, run_checks
from recon_engine.match import match
from recon_engine.models import Assumption, Certificate, CertificateStatus, MatchSpec

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def certified(kind="clean", plan=None, store=None):
    manifest, ctx = fixture_context(kind, store)
    buckets = run_checks(ctx, plan if plan is not None else plan_for(kind, manifest))
    return manifest, ctx, buckets, certify(ctx, buckets, now=NOW)


def test_the_clean_fixture_balances_but_is_provisional_until_confirmed():
    manifest, _, _, cert = certified()

    assert cert.status == CertificateStatus.PROVISIONAL
    assert cert.residual == 0
    assert cert.residual_items == []
    assert cert.explained == cert.match.gap == Decimal(manifest["expected_gap"])
    assert len(cert.buckets) == 6


def test_certificate_records_versions_extracts_and_spec():
    _, ctx, _, cert = certified()

    assert cert.engine_version == __version__
    assert {b.check_name: b.check_version for b in cert.buckets}["duplicates"] == "1.1.0"
    assert [e.data_hash for e in cert.extracts] == [
        ctx.a.extract.data_hash,
        ctx.b.extract.data_hash,
    ]
    assert cert.extracts[0].query_text == "FILE dashboard_oct2026.csv"
    assert cert.match_spec == ctx.spec
    assert cert.created_at == NOW


def test_unconfirmed_assumptions_are_listed_on_the_certificate():
    _, _, _, cert = certified()

    referenced = {i for b in cert.buckets for i in b.assumption_ids}
    assert {a.id for a in cert.assumptions} == referenced
    assert set(cert.unconfirmed_assumptions) == referenced
    assert cert.assumptions_confirmed is False


def test_confirmed_assumptions_clear_the_flag_on_a_new_certificate():
    store = AssumptionStore()
    _, ctx, buckets, first = certified(store=store)
    for assumption_id in first.unconfirmed_assumptions:
        store.confirm(assumption_id, "Jane CFO", at=NOW)
    second = certify(ctx, buckets, now=NOW)

    assert first.unconfirmed_assumptions  # the earlier certificate is unchanged
    assert first.status == CertificateStatus.PROVISIONAL
    assert second.unconfirmed_assumptions == []
    assert second.status == CertificateStatus.RECONCILED
    assert second.assumptions_confirmed is True
    assert all(a.confirmed_by == "Jane CFO" for a in second.assumptions)


def test_the_messy_fixture_is_open_with_the_honest_residual():
    manifest, _, _, cert = certified("messy")

    assert cert.status == CertificateStatus.OPEN
    assert cert.residual == Decimal(manifest["residual"]["amount"])
    assert len(cert.residual_items) == len(manifest["residual"]["items"])
    assert sum(i.amount for i in cert.residual_items) == cert.residual


def test_a_partial_explanation_is_open():
    _, ctx, _, cert = certified(plan=[CLEAN_PLAN["timing"]])

    assert cert.status == CertificateStatus.OPEN
    assert cert.residual == ctx.result.gap - Decimal(26900)


def test_items_netting_to_zero_still_leave_the_certificate_open():
    a = make_extract([("INV-1 ", "2026-10-03", "5.00", "PAID")], "a")
    b = make_extract([("INV-1", "2026-10-03", "5.00", "PAID")], "b")
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_id"],
        amount_column="amount",
    )
    ctx = CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))
    cert = certify(ctx, [], now=NOW)

    assert cert.residual == 0
    assert len(cert.residual_items) == 2
    assert cert.status == CertificateStatus.OPEN


def test_checks_with_no_findings_are_listed_without_their_assumptions():
    plan = [*plan_for("clean", None), ("tax_basis", {"rate": "0.15", "inclusive_side": "b"})]
    _, _, buckets, cert = certified(plan=plan)

    assert len(buckets) == 7
    assert len(cert.buckets) == 6
    [empty] = cert.checks_without_findings
    assert empty.check_name == "tax_basis"
    assert empty.parameters == {"rate": "0.15", "inclusive_side": "b"}
    assert buckets[-1].assumption_ids[0] not in cert.unconfirmed_assumptions
    assert buckets[-1].assumption_ids[0] not in {a.id for a in cert.assumptions}


def test_the_hash_verifies_and_survives_a_json_round_trip():
    _, _, _, cert = certified()

    assert verify_certificate(cert)
    reloaded = Certificate.model_validate_json(cert.model_dump_json())
    assert verify_certificate(reloaded)
    assert reloaded.certificate_hash == cert.certificate_hash
    assert cert.id == f"cert_{cert.certificate_hash[:16]}"


def test_the_same_inputs_give_the_same_certificate():
    _, ctx, buckets, cert = certified()

    assert certify(ctx, buckets, now=NOW).certificate_hash == cert.certificate_hash


def test_any_change_breaks_the_hash():
    _, _, _, cert = certified()

    assert not verify_certificate(cert.model_copy(update={"residual": Decimal("0.01")}))
    assert not verify_certificate(cert.model_copy(update={"status": CertificateStatus.OPEN}))


def test_a_bucket_whose_amount_was_altered_is_refused():
    _, ctx, buckets, _ = certified()
    forged = buckets[0].model_copy(update={"amount": buckets[0].amount + 1})

    with pytest.raises(ConservationError, match="does not equal the sum of its items"):
        certify(ctx, [forged, *buckets[1:]], now=NOW)


def test_overlapping_buckets_are_refused():
    _, ctx = fixture_context()
    name, params = CLEAN_PLAN["status_filter"]
    first = get_check(name).run(ctx, params)
    second = get_check(name).run(ctx, params)  # same context, so it claims the same items

    with pytest.raises(ConservationError, match="in more than one bucket"):
        certify(ctx, [first, second], now=NOW)


def test_a_bucket_from_another_reconciliation_is_refused():
    _, ctx, _, _ = certified()
    _, _, other_buckets, _ = certified("messy")

    with pytest.raises(ConservationError, match="unknown item"):
        certify(ctx, other_buckets[:1], now=NOW)


def test_a_bucket_whose_rows_do_not_match_its_items_is_refused():
    _, ctx, buckets, _ = certified()
    forged = buckets[1].model_copy(update={"row_ids": buckets[1].row_ids[:-1], "row_count": 2})

    with pytest.raises(ConservationError, match="rows do not match its items"):
        certify(ctx, [buckets[0], forged, *buckets[2:]], now=NOW)


def test_assumptions_must_be_in_the_store():
    _, ctx, buckets, _ = certified()
    detached = CheckContext(a=ctx.a, b=ctx.b, spec=ctx.spec, result=ctx.result)

    with pytest.raises(ConservationError, match="unknown assumption"):
        certify(detached, buckets, now=NOW)


def test_supersedes_is_recorded_and_hashed():
    _, ctx, buckets, first = certified()
    second = certify(ctx, buckets, now=NOW, supersedes=first.id)

    assert first.supersedes is None
    assert second.supersedes == first.id
    assert second.certificate_hash != first.certificate_hash
    assert verify_certificate(second)


def test_open_items_outrank_unconfirmed_assumptions():
    _, _, _, cert = certified("messy")

    assert cert.unconfirmed_assumptions
    assert cert.status == CertificateStatus.OPEN


def test_extra_session_assumptions_are_part_of_the_certificate():
    store = AssumptionStore()
    _, ctx, buckets, _ = certified(store=store)
    for assumption in store.all():
        store.confirm(assumption.id, "Jane CFO", at=NOW)
    extra = store.propose(
        Assumption.propose("scope.entity", "AU01", "Only the AU01 entity is in scope.")
    )
    cert = certify(ctx, buckets, now=NOW, extra_assumption_ids=[extra.id])

    assert extra.id in cert.unconfirmed_assumptions
    assert cert.status == CertificateStatus.PROVISIONAL


def test_empty_amounts_are_unexplained_data_quality_items():
    a = make_extract(
        [("INV-1", "2026-10-03", None, "PAID"), ("INV-2", "2026-10-04", "7", "PAID")], "a"
    )
    b = make_extract([("INV-2", "2026-10-04", "7", "PAID")], "b")
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_id"],
        amount_column="amount",
    )
    ctx = CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))
    cert = certify(ctx, [], now=NOW)

    [item] = cert.residual_items
    assert item.kind == "data_quality"
    assert item.amount == 0
    assert "empty" in item.reason
    assert cert.residual == 0
    assert cert.status == CertificateStatus.OPEN
    assert cert.match.invalid_a_count == 1
