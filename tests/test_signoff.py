"""A RECONCILED certificate is final only once a person signs it off."""

import json

import pytest
from support import CONFIRMER, PASSPHRASE, fixture_extracts, plan_for, signer
from typer.testing import CliRunner

from tieout_ai import cli
from tieout_ai.cli import app
from tieout_ai.project import DENIED_COMMANDS, DENY
from tieout_ai.render import render_html
from tieout_ai.service import ReconService
from tieout_ai.session import Session, Workspace
from tieout_ai.signing import Signer

CODE = "K7Q2PX"
runner = CliRunner()


def recon(home, *args, input=None):
    return runner.invoke(app, ["--home", str(home), *args], input=input)


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_confirmation_code", lambda: CODE)


def issue(home, kind="clean", confirm=True):
    manifest, a, b, spec = fixture_extracts(kind)
    session = Session(Workspace(home), a, b, spec)
    for name, params in plan_for(kind, manifest):
        session.run_check(name, params)
    if confirm:
        for assumption in session.workspace.assumptions.all():
            session.workspace.assumptions.confirm(assumption.id, signer())
    return session.certify()


SIGN = f"{CODE}\n{PASSPHRASE}\n"


def test_a_reconciled_certificate_is_not_final_until_signed_off(tmp_path):
    cert = issue(tmp_path)
    summary = ReconService(Workspace(tmp_path)).get_certificate(cert.id)

    assert cert.status.value == "RECONCILED"
    assert summary["signed_off"] is False
    assert summary["final"] is False
    assert summary["sign_off_status"] == "not signed off"
    assert "tieout cert sign" in summary["next_step"]


def test_signing_off_makes_it_final(tmp_path, interactive):
    cert = issue(tmp_path)
    signed = recon(tmp_path, "cert", "sign", cert.id, input=SIGN)
    summary = ReconService(Workspace(tmp_path)).get_certificate(cert.id)

    assert signed.exit_code == 0, signed.output
    assert summary["signed_off"] is True
    assert summary["final"] is True
    assert summary["sign_off"]["by"] == CONFIRMER
    assert summary["sign_off"]["fingerprint"] == signer().trusted.fingerprint


def test_cert_show_and_html_say_whether_it_is_signed_off(tmp_path, interactive):
    cert = issue(tmp_path)
    before = recon(tmp_path, "cert", "show", cert.id)
    assert "NOT SIGNED OFF" in before.output
    assert "Not signed off" in render_html(cert, Workspace(tmp_path).sign_off(cert))

    recon(tmp_path, "cert", "sign", cert.id, input=SIGN)
    after = recon(tmp_path, "cert", "show", cert.id)
    html = (tmp_path / "certificates" / f"{cert.id}.html").read_text(encoding="utf-8")

    assert f"SIGNED OFF by {CONFIRMER}" in after.output
    assert f"Signed off by {CONFIRMER}" in html


@pytest.mark.parametrize(
    ("kind", "confirm", "status"), [("clean", False, "PROVISIONAL"), ("messy", False, "OPEN")]
)
def test_only_reconciled_certificates_can_be_signed_off(
    tmp_path, interactive, kind, confirm, status
):
    cert = issue(tmp_path, kind, confirm)
    result = recon(tmp_path, "cert", "sign", cert.id, input=SIGN)

    assert cert.status.value == status
    assert result.exit_code == 1
    assert f"Only a RECONCILED certificate can be signed off; this one is {status}" in result.output


def test_sign_needs_a_terminal_the_code_and_the_passphrase(tmp_path, monkeypatch):
    cert = issue(tmp_path)
    assert "interactive terminal" in recon(tmp_path, "cert", "sign", cert.id, input=SIGN).output

    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_confirmation_code", lambda: CODE)
    assert recon(tmp_path, "cert", "sign", cert.id, input="WRONG\n").exit_code == 1
    assert (
        "Wrong passphrase"
        in recon(tmp_path, "cert", "sign", cert.id, input=f"{CODE}\nnope nope\n").output
    )
    assert Workspace(tmp_path).sign_off(cert).status == "not signed off"


def test_a_hand_written_sign_off_does_not_count(tmp_path):
    cert = issue(tmp_path)
    path = Workspace(tmp_path).sign_off_path(cert.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "certificate_id": cert.id,
                "certificate_hash": cert.certificate_hash,
                "status": "RECONCILED",
                "signature": {
                    "by": CONFIRMER,
                    "at": "2026-11-17T09:00:00+00:00",
                    "fingerprint": signer().trusted.fingerprint,
                    "value": "AAAA",
                },
            }
        ),
        encoding="utf-8",
    )
    sign_off = Workspace(tmp_path).sign_off(cert)

    assert sign_off.status == "sign-off does not verify"
    assert ReconService(Workspace(tmp_path)).get_certificate(cert.id)["final"] is False


def test_a_sign_off_by_another_key_does_not_count(tmp_path):
    cert = issue(tmp_path)
    Workspace(tmp_path).save_sign_off(cert, Signer.generate(CONFIRMER))

    sign_off = Workspace(tmp_path).sign_off(cert)
    assert sign_off.status == "sign-off does not verify"
    assert "not this machine's confirmer key" in sign_off.problem


def test_a_sign_off_cannot_be_moved_to_another_certificate(tmp_path):
    first = issue(tmp_path / "one")
    second = issue(tmp_path / "two")
    Workspace(tmp_path / "one").save_sign_off(first, signer())
    moved = Workspace(tmp_path / "two").sign_off_path(second.id)
    moved.parent.mkdir(parents=True, exist_ok=True)
    moved.write_text(
        Workspace(tmp_path / "one").sign_off_path(first.id).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert first.id != second.id
    assert Workspace(tmp_path / "two").sign_off(second).status == "sign-off does not verify"


def test_cert_list_ignores_sign_off_files(tmp_path):
    cert = issue(tmp_path)
    Workspace(tmp_path).save_sign_off(cert, signer())

    assert Workspace(tmp_path).list_certificates() == [cert.id]


def test_recon_init_denies_cert_sign_to_the_ai():
    assert "cert sign" in DENIED_COMMANDS
    assert "Bash(tieout cert sign*)" in DENY
    assert "PowerShell(*tieout cert sign*)" in DENY
