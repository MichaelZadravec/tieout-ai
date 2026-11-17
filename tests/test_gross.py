"""Certificates show both the net residual and the gross unexplained amount."""

import json
from decimal import Decimal
from pathlib import Path

from support import FIXTURE_MATCH_ARGS, fixture_context, fixture_workspace, plan_for
from typer.testing import CliRunner

from tieout_ai.certify import certify
from tieout_ai.checks import run_checks
from tieout_ai.cli import app
from tieout_ai.models import Certificate
from tieout_ai.render import render_html
from tieout_ai.service import ReconService

LEGACY = Path(__file__).parent / "data" / "certificate_v0_pre_4b.json"


def messy_cert():
    manifest, ctx = fixture_context("messy")
    return manifest, certify(ctx, run_checks(ctx, plan_for("messy", manifest)))


def test_gross_is_the_sum_of_absolute_open_amounts():
    manifest, cert = messy_cert()
    expected = sum(abs(Decimal(r["amount"])) for r in manifest["residual"]["items"])

    assert cert.residual == Decimal(manifest["residual"]["amount"])
    assert cert.gross_unexplained == expected
    assert cert.gross_unexplained > cert.residual  # the trailing-space pair nets to $0


def test_a_fully_explained_certificate_has_zero_gross():
    manifest, ctx = fixture_context("clean")
    cert = certify(ctx, run_checks(ctx, plan_for("clean", manifest)))

    assert cert.residual == 0
    assert cert.gross_unexplained == 0


def test_html_shows_net_and_gross():
    _, cert = messy_cert()
    html = render_html(cert)

    assert "Unexplained (net)" in html
    assert "Unexplained (gross)" in html
    assert "$1,326.00" in html
    assert f"${cert.gross_unexplained:,.2f}" in html


def test_cli_shows_net_and_gross(tmp_path):
    service = ReconService(fixture_workspace(tmp_path, "messy"))
    a = service.run_query("dashboard")["extract_id"]
    b = service.run_query("xero")["extract_id"]
    rec = service.match(a, b, **FIXTURE_MATCH_ARGS)["reconciliation_id"]
    manifest, _ = fixture_context("messy")
    for name, params in plan_for("messy", manifest):
        service.run_check(rec, name, params)
    summary = service.certify(rec)
    shown = CliRunner().invoke(
        app, ["--home", str(tmp_path), "cert", "show", summary["certificate_id"]]
    )

    assert shown.exit_code == 0, shown.output
    assert "Unexplained (net)" in shown.output
    assert "Unexplained (gross)" in shown.output
    assert summary["residual"] == "1326.00"
    assert Decimal(summary["gross_unexplained"]) > Decimal(summary["residual"])


def test_an_earlier_certificate_without_the_field_shows_gross_from_its_items():
    cert = Certificate.model_validate(json.loads(LEGACY.read_text(encoding="utf-8")))

    assert cert.gross_unexplained is None
    assert cert.gross == sum((abs(i.amount) for i in cert.residual_items), Decimal(0))
    assert "Unexplained (gross)" in render_html(cert)
