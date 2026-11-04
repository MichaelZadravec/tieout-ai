import json

import pytest
from support import fixture_extracts, plan_for
from typer.testing import CliRunner

from recon_engine.cli import app
from recon_engine.session import Session, Workspace

runner = CliRunner()


@pytest.fixture
def home(tmp_path):
    return tmp_path / "recon-home"


@pytest.fixture
def cert(home):
    manifest, a, b, spec = fixture_extracts("clean")
    session = Session(Workspace(home), a, b, spec)
    for name, params in plan_for("clean", manifest):
        session.run_check(name, params)
    return session.certify()


def recon(home, *args):
    return runner.invoke(app, ["--home", str(home), *args])


def test_session_saves_the_certificate_and_assumptions(home, cert):
    assert (home / "certificates" / f"{cert.id}.json").exists()
    assert len(Workspace(home).assumptions) == len(cert.assumptions)


def test_assumptions_list_shows_ids_and_state(home, cert):
    result = recon(home, "assumptions", "list")

    assert result.exit_code == 0, result.output
    for assumption in cert.assumptions:
        assert assumption.id in result.output
    assert "unconfirmed" in result.output


def test_confirm_records_the_human_and_persists(home, cert):
    target = cert.unconfirmed_assumptions[0]
    result = recon(home, "confirm", target, "--by", "Jane CFO")

    assert result.exit_code == 0, result.output
    assert "Confirmed" in result.output
    stored = Workspace(home).assumptions.get(target)
    assert stored.confirmed is True
    assert stored.confirmed_by == "Jane CFO"


def test_confirm_unknown_assumption_fails_cleanly(home, cert):
    result = recon(home, "confirm", "asm_nope", "--by", "Jane CFO")

    assert result.exit_code == 1
    assert "No assumption with id 'asm_nope'" in result.output


def test_cert_list_and_show(home, cert):
    listed = recon(home, "cert", "list")
    shown = recon(home, "cert", "show", cert.id)

    assert listed.exit_code == 0 and cert.id in listed.output
    assert shown.exit_code == 0, shown.output
    assert "RECONCILED" in shown.output
    assert "29,072.00" in shown.output
    assert "UNCONFIRMED" in shown.output
    assert "Hash verified" in shown.output


def test_cert_show_notes_assumptions_confirmed_after_issue(home, cert):
    recon(home, "confirm", cert.unconfirmed_assumptions[0], "--by", "Jane CFO")
    shown = recon(home, "cert", "show", cert.id)

    assert "confirmed since this certificate was issued" in shown.output


def test_cert_show_writes_html_and_json(home, cert, tmp_path):
    html_path, json_path = tmp_path / "cert.html", tmp_path / "cert.json"
    result = recon(
        home, "cert", "show", cert.id, "--html", str(html_path), "--json", str(json_path)
    )

    assert result.exit_code == 0, result.output
    assert "RECONCILED" in html_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["id"] == cert.id


def test_cert_show_unknown_id_fails_cleanly(home, cert):
    result = recon(home, "cert", "show", "cert_nope")

    assert result.exit_code == 1
    assert "No certificate 'cert_nope'" in result.output


def test_a_tampered_certificate_file_is_reported(home, cert):
    path = home / "certificates" / f"{cert.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["residual"] = "-1.00"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = recon(home, "cert", "show", cert.id)
    assert result.exit_code == 1
    assert "does not match its hash" in result.output


def test_version_still_works():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
