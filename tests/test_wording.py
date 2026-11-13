"""User-facing wording: real source names, and assumptions scoped to the rows they cover."""

import re

import pytest
from support import fixture_context, make_extract, plan_for
from typer.testing import CliRunner

from recon_engine.certify import certify
from recon_engine.checks import CheckContext, CheckParameterError, get_check, run_checks
from recon_engine.cli import app
from recon_engine.match import MatchError, match
from recon_engine.models import MatchSpec, key_label, source_names
from recon_engine.render import MINUS, render_html
from recon_engine.session import Session, Workspace

SIDE_WORDING = re.compile(r"\b[Ss]ide [AB]\b|\bin [AB]\b|\([AB] (&minus;|" + MINUS + r"|-) [AB]\)")


@pytest.fixture
def clean():
    manifest, ctx = fixture_context("clean")
    buckets = run_checks(ctx, plan_for("clean", manifest))
    return ctx, buckets


def test_every_assumption_names_the_sources_not_sides(clean):
    ctx, buckets = clean
    for bucket in buckets:
        for assumption in ctx.assumptions.get_many(bucket.assumption_ids):
            assert not SIDE_WORDING.search(assumption.description), assumption.description
            assert "dashboard" in assumption.description or "xero" in assumption.description


def test_every_assumption_is_scoped_to_the_rows_its_bucket_claims(clean):
    ctx, buckets = clean
    for bucket in buckets:
        keys = sorted({key_label(ctx.item(i).key) for i in bucket.item_ids})
        for assumption in ctx.assumptions.get_many(bucket.assumption_ids):
            assert assumption.scope == keys, bucket.check_name
            assert keys[0] in assumption.description
            assert f"{len(keys)} records" in assumption.description or len(keys) == 1


def test_tax_basis_wording_matches_what_was_proven(clean):
    ctx, buckets = clean
    [tax] = [b for b in buckets if b.check_name == "tax_basis"]
    [assumption] = ctx.assumptions.get_many(tax.assumption_ids)
    keys = assumption.scope

    assert assumption.description.startswith(f"For these {len(keys)} records (")
    assert "xero amounts include 10% tax and dashboard amounts exclude it" in (
        assumption.description
    )


def test_long_scopes_show_first_and_last_key(clean):
    ctx, buckets = clean
    [timing] = [b for b in buckets if b.check_name == "timing"]
    assumption = ctx.assumptions.get(timing.assumption_ids[0])
    first, last = assumption.scope[0], assumption.scope[-1]

    assert len(assumption.scope) == 58
    assert f"For these 58 records ({first} … {last})" in assumption.description


def test_a_single_record_reads_naturally():
    a = make_extract([("INV-1", "2026-10-03", "110.00", "PAID")], "dashboard")
    b = make_extract([("INV-1", "2026-10-03", "100.00", "PAID")], "xero")
    spec = MatchSpec(
        extract_a=a.extract.id, extract_b=b.extract.id, key_columns=["invoice_id"],
        amount_column="amount",
    )  # fmt: skip
    ctx = CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))
    bucket = get_check("tax_basis").run(ctx, {"rate": "0.10", "inclusive_side": "a"})
    [assumption] = ctx.assumptions.get_many(bucket.assumption_ids)

    assert assumption.description.startswith("For this record (INV-1), dashboard amounts")


def test_assumption_ids_depend_on_the_scope():
    _, ctx = fixture_context("clean")
    params = {"rate": "0.10", "inclusive_side": "b"}
    full = get_check("tax_basis").run(ctx, params)
    again = get_check("tax_basis").run(ctx, params)
    narrower = get_check("tax_basis").run(ctx.with_claimed(full.item_ids[:1]), params)

    assert full.assumption_ids == again.assumption_ids
    assert narrower.item_ids == full.item_ids[1:]
    assert set(narrower.assumption_ids).isdisjoint(full.assumption_ids)


def test_a_check_that_finds_nothing_proposes_no_assumptions():
    _, ctx = fixture_context("clean")
    bucket = get_check("tax_basis").run(ctx, {"rate": "0.15", "inclusive_side": "b"})

    assert bucket.item_ids == []
    assert bucket.assumption_ids == []
    assert len(ctx.assumptions) == 0


def test_certificate_html_and_cli_use_source_names(tmp_path, clean):
    ctx, buckets = clean
    html = render_html(certify(ctx, buckets))

    assert not SIDE_WORDING.search(html), SIDE_WORDING.search(html)
    assert "dashboard total" in html
    assert "xero total" in html
    _, messy = fixture_context("messy")
    unexplained = render_html(certify(messy, []))
    assert not SIDE_WORDING.search(unexplained), SIDE_WORDING.search(unexplained)
    assert "Only in dashboard" in unexplained
    assert "Only in xero" in unexplained

    from support import fixture_extracts

    manifest, a, b, spec = fixture_extracts("clean")
    session = Session(Workspace(tmp_path), a, b, spec)
    for name, params in plan_for("clean", manifest):
        session.run_check(name, params)
    cert = session.certify()
    shown = CliRunner().invoke(app, ["--home", str(tmp_path), "cert", "show", cert.id])

    assert shown.exit_code == 0, shown.output
    assert not SIDE_WORDING.search(shown.output), shown.output
    assert "dashboard total" in shown.output


def test_the_same_connection_on_both_sides_is_told_apart():
    assert source_names("ledger", "ledger") == ("ledger (A)", "ledger (B)")
    assert source_names("dashboard", "xero") == ("dashboard", "xero")


def test_errors_name_the_source():
    a = make_extract([("INV-1", "2026-10-03", "1", "PAID")], "dashboard")
    b = make_extract([("INV-1", "2026-10-03", "1", "PAID")], "xero")
    spec = MatchSpec(
        extract_a=a.extract.id, extract_b=b.extract.id, key_columns=["invoice_id"],
        amount_column="amount", amount_column_b="Total",
    )  # fmt: skip
    with pytest.raises(MatchError, match=r"^xero .* has no column 'Total'"):
        match(a, b, spec)

    ok = spec.model_copy(update={"amount_column_b": None})
    ctx = CheckContext(a=a, b=b, spec=ok, result=match(a, b, ok))
    with pytest.raises(CheckParameterError, match=r"^xero has no column 'nope'"):
        get_check("status_filter").run(
            ctx, {"side": "b", "status_column": "nope", "statuses": ["X"]}
        )
