"""The confirmer key's fingerprint, shown so a reader can check it against the confirmer's own."""

import json
from pathlib import Path

from support import CONFIRMER, fixture_extracts, plan_for, signer
from typer.testing import CliRunner

from tieout_ai.cli import app
from tieout_ai.render import render_html
from tieout_ai.service import ReconService
from tieout_ai.session import Session, Workspace

LEGACY = Path(__file__).parent / "data" / "certificate_v0_pre_4b.json"
runner = CliRunner()


def issue(home):
    manifest, a, b, spec = fixture_extracts("clean")
    session = Session(Workspace(home), a, b, spec)
    for name, params in plan_for("clean", manifest):
        session.run_check(name, params)
    for assumption in session.workspace.assumptions.all():
        session.workspace.assumptions.confirm(assumption.id, signer())
    return session.certify()


def test_keys_fingerprint_prints_just_the_fingerprint():
    result = runner.invoke(app, ["keys", "fingerprint"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == signer().trusted.fingerprint


def test_keys_fingerprint_without_a_key_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("TIEOUT_KEYS_DIR", str(tmp_path / "none"))
    result = runner.invoke(app, ["keys", "fingerprint"])

    assert result.exit_code == 1
    assert "tieout keys init" in result.output


def test_the_html_shows_the_expected_fingerprint_before_anything_else(tmp_path):
    cert = issue(tmp_path)
    html = render_html(cert)
    panel = html.index('class="fingerprint"')

    assert cert.confirmer_key_fingerprint in html[panel : html.index("How the gap is explained")]
    assert panel < html.index('class="tiles"')
    assert "tieout keys fingerprint" in html
    assert CONFIRMER in html[panel : panel + 600]


def test_cert_show_leads_with_the_expected_fingerprint(tmp_path):
    cert = issue(tmp_path)
    shown = runner.invoke(app, ["--home", str(tmp_path), "cert", "show", cert.id])
    head = "\n".join(shown.output.splitlines()[:6])

    assert f"Expected confirmer key: {cert.confirmer_key_fingerprint} ({CONFIRMER})" in head
    assert "tieout keys fingerprint" in head


def test_get_certificate_reports_the_fingerprint(tmp_path):
    cert = issue(tmp_path)
    summary = ReconService(Workspace(tmp_path)).get_certificate(cert.id)

    assert summary["confirmer_key_fingerprint"] == cert.confirmer_key_fingerprint
    assert summary["confirmer"] == CONFIRMER


def test_a_certificate_without_a_recorded_key_says_so(tmp_path):
    doc = json.loads(LEGACY.read_text(encoding="utf-8"))
    from tieout_ai.models import Certificate

    html = render_html(Certificate.model_validate(doc))

    assert "No confirmer key is recorded" in html
