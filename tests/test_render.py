from datetime import UTC, datetime

import pandas as pd
from support import COLUMNS, INVOICES, fixture_context, plan_for

from recon_engine.assumptions import AssumptionStore
from recon_engine.certify import certify
from recon_engine.checks import CheckContext, run_checks
from recon_engine.connectors.base import build_extract
from recon_engine.match import match
from recon_engine.models import MatchSpec
from recon_engine.render import MINUS, render_html

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def html_for(kind, store=None, confirm=False):
    manifest, ctx = fixture_context(kind, store or AssumptionStore())
    buckets = run_checks(ctx, plan_for(kind, manifest))
    if confirm:
        for assumption in ctx.assumptions.all():
            ctx.assumptions.confirm(assumption.id, "Jane CFO", at=NOW)
    cert = certify(ctx, buckets, now=NOW)
    return cert, render_html(cert)


def test_html_headline_shows_status_gap_and_residual():
    cert, html = html_for("clean")

    assert "<!doctype html>" in html.lower()
    assert "PROVISIONAL" in html
    assert "$29,072.00" in html
    assert cert.id in html
    assert cert.certificate_hash in html


def test_html_lists_every_bucket_with_amount_and_count():
    _, html = html_for("clean")

    for name in ("timing", "status_filter", "tax_basis", "duplicates", "sign_credit"):
        assert name in html
    assert "$26,900.00" in html
    assert f"{MINUS}$379.00" in html


def test_unconfirmed_assumptions_are_flagged_prominently():
    cert, html = html_for("clean")

    assert "Unconfirmed assumptions" in html
    assert f"{len(cert.unconfirmed_assumptions)} assumption" in html
    for assumption in cert.assumptions:
        assert assumption.id in html


def test_fully_confirmed_certificate_says_so():
    _, html = html_for("clean", confirm=True)

    assert "Unconfirmed assumptions" not in html
    assert "All assumptions confirmed" in html
    assert "RECONCILED" in html
    assert "PROVISIONAL" not in html
    assert "Jane CFO" in html


def test_open_certificate_shows_the_unexplained_items():
    cert, html = html_for("messy")

    assert "OPEN" in html
    assert "Unexplained" in html
    assert f"${cert.residual:,.2f}" in html
    assert "INV-20" in html  # residual items are listed by key
    assert "(blank)" in html  # the row with no invoice number
    assert any(f"&quot;{r['key']}&quot;" in html for r in manifest_keys_with_spaces())
    assert len(cert.residual_items) > 0


def test_sources_and_queries_are_shown_and_escaped():
    frame = pd.DataFrame([list(r) for r in INVOICES], columns=COLUMNS, dtype=object)
    a = build_extract("ledger", "SELECT '<script>alert(1)</script>' AS x", frame)
    b = build_extract("dashboard", "FILE dash.csv", frame)
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_id"],
        amount_column="amount",
    )
    ctx = CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))
    html = render_html(certify(ctx, [], now=NOW))

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert a.extract.data_hash in html
    assert "ledger" in html


def manifest_keys_with_spaces():
    import json

    from support import FIXTURE_DATA

    manifest = json.loads((FIXTURE_DATA / "manifest_messy.json").read_text(encoding="utf-8"))
    return [r for r in manifest["residual"]["items"] if r["key"] and r["key"] != r["key"].strip()]


def test_checks_with_no_findings_get_a_compact_line_and_no_warning():
    store = AssumptionStore()
    manifest, ctx = fixture_context("clean", store)
    plan = [*plan_for("clean", manifest), ("duplicates", {"side": "b"})]
    buckets = run_checks(ctx, plan)
    used = {i for b in buckets[:-1] for i in b.assumption_ids}
    for assumption_id in used:
        store.confirm(assumption_id, "Jane CFO", at=NOW)
    unused = [i for i in buckets[-1].assumption_ids if i not in used]
    html = render_html(certify(ctx, buckets, now=NOW))

    assert unused  # the empty bucket's own assumption was never confirmed...
    assert "Checks with no findings" in html
    assert "side=b" in html
    assert "Unconfirmed assumptions" not in html  # ...and raises no warning
    assert all(i not in html for i in unused)
    assert "RECONCILED" in html


def test_data_quality_items_are_labelled():
    frame = pd.DataFrame(
        [["INV-1", "2026-10-03", None, "PAID"], ["INV-2", "2026-10-03", "4", "PAID"]],
        columns=COLUMNS,
        dtype=object,
    )
    a = build_extract("a", "FILE a.csv", frame)
    b = build_extract("b", "FILE b.csv", frame.iloc[1:])
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_id"],
        amount_column="amount",
    )
    ctx = CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))
    html = render_html(certify(ctx, [], now=NOW))

    assert "Data quality" in html
    assert "amount is empty" in html
