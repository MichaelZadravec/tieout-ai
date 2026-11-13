"""Elevated assumptions: any parameter that loosens a check's default is flagged."""

from html import escape

import pytest
from support import fixture_context, fixture_extracts, plan_for
from typer.testing import CliRunner

from recon_engine.certify import certify
from recon_engine.checks import get_check, run_checks
from recon_engine.cli import app
from recon_engine.render import render_html
from recon_engine.session import Session, Workspace


def assumptions_of(ctx, bucket):
    return {a.parameter: a for a in ctx.assumptions.get_many(bucket.assumption_ids)}


def test_status_variants_are_elevated():
    _, ctx = fixture_context("messy")
    params = {"side": "a", "status_column": "status", "statuses": ["VOIDED", "Voided"]}
    found = assumptions_of(ctx, get_check("status_filter").run(ctx, params))
    assumption = found["status_filter.statuses"]

    assert assumption.elevated is True
    assert (
        assumption.elevated_reason == "loosens the default: counts 'VOIDED', 'Voided' as one status"
    )


def test_one_status_value_is_not_elevated():
    _, ctx = fixture_context("clean")
    params = {"side": "a", "status_column": "status", "statuses": ["VOIDED"]}
    found = assumptions_of(ctx, get_check("status_filter").run(ctx, params))

    assert found["status_filter.statuses"].elevated is False
    assert found["status_filter.statuses"].elevated_reason is None


def test_two_different_statuses_are_not_variants():
    _, ctx = fixture_context("clean")
    params = {"side": "a", "status_column": "status", "statuses": ["VOIDED", "DRAFT"]}
    bucket = get_check("status_filter").run(ctx, params)

    assert not any(a.elevated for a in assumptions_of(ctx, bucket).values())


def test_narrow_compare_columns_are_elevated():
    _, ctx = fixture_context("messy")
    compare = ["invoice_number", "appointment_date", "amount", "status", "type"]
    found = assumptions_of(
        ctx, get_check("duplicates").run(ctx, {"side": "a", "compare_columns": compare})
    )

    assert found["duplicates.compare_columns"].elevated is True
    assert found["duplicates.compare_columns"].elevated_reason == (
        "loosens the default: ignores columns load_id, loaded_at"
    )
    assert found["duplicates.side"].elevated is False


def test_comparing_every_column_is_not_elevated():
    _, ctx = fixture_context("clean")
    bucket = get_check("duplicates").run(ctx, {"side": "a"})

    assert bucket.item_ids
    assert not any(a.elevated for a in assumptions_of(ctx, bucket).values())


def test_timing_wider_than_the_default_window_is_elevated():
    _, ctx = fixture_context("clean")
    found = assumptions_of(ctx, get_check("timing").run(ctx, {"max_shift_days": 31}))

    assert found["timing.max_shift_days"].elevated is True
    assert found["timing.max_shift_days"].elevated_reason == (
        "loosens the default: allows dates up to 31 days apart (default 7)"
    )
    assert found["timing.date_basis"].elevated is False


def test_timing_within_the_default_window_is_not_elevated():
    _, ctx = fixture_context("clean")
    bucket = get_check("timing").run(ctx, {})

    assert bucket.parameters == {"max_shift_days": 7}
    assert not any(a.elevated for a in assumptions_of(ctx, bucket).values())


@pytest.fixture
def messy_cert(tmp_path):
    manifest, a, b, spec = fixture_extracts("messy")
    session = Session(Workspace(tmp_path), a, b, spec)
    for name, params in plan_for("messy", manifest):
        session.run_check(name, params)
    return tmp_path, session.certify()


def elevated_of(cert):
    return {a.parameter: a for a in cert.assumptions if a.elevated}


def test_messy_certificate_shows_elevated_assumptions_first_in_the_html(messy_cert):
    _, cert = messy_cert
    elevated = elevated_of(cert)
    html = render_html(cert)
    section = html.index("Elevated assumptions")

    assert {"status_filter.statuses", "duplicates.compare_columns"} <= set(elevated)
    assert section < html.index("How the gap is explained")
    for assumption in elevated.values():
        reason = escape(assumption.elevated_reason, quote=True)
        assert section < html.index(reason) < html.index("How the gap")


def test_cli_lists_elevated_assumptions_separately_with_reasons(messy_cert):
    home, cert = messy_cert
    listed = CliRunner().invoke(app, ["--home", str(home), "assumptions", "list"])
    shown = CliRunner().invoke(app, ["--home", str(home), "cert", "show", cert.id])

    assert listed.exit_code == 0, listed.output
    assert listed.output.startswith("ELEVATED")
    elevated_part, _, rest = listed.output.partition("\nOther assumptions")
    for assumption in elevated_of(cert).values():
        assert assumption.id in elevated_part
        assert assumption.elevated_reason in elevated_part
        assert assumption.id not in rest
    assert shown.exit_code == 0, shown.output
    assert "ELEVATED" in shown.output
    assert "counts 'VOIDED', 'Voided' as one status" in shown.output


def test_run_check_reports_elevation_to_the_ai(tmp_path):
    from support import FIXTURE_MATCH_ARGS, fixture_workspace

    from recon_engine.service import ReconService

    service = ReconService(fixture_workspace(tmp_path, "messy"))
    a = service.run_query("dashboard")["extract_id"]
    b = service.run_query("xero")["extract_id"]
    rec = service.match(a, b, **FIXTURE_MATCH_ARGS)["reconciliation_id"]
    result = service.run_check(
        rec,
        "status_filter",
        {"side": "a", "status_column": "status", "statuses": ["VOIDED", "Voided"]},
    )
    [assumption] = result["assumptions"]

    assert assumption["elevated"] is True
    assert "counts 'VOIDED', 'Voided'" in assumption["elevated_reason"]


def test_elevation_does_not_change_the_status_rules():
    manifest, ctx = fixture_context("clean")
    cert = certify(ctx, run_checks(ctx, plan_for("clean", manifest)))

    assert cert.status.value == "PROVISIONAL"
