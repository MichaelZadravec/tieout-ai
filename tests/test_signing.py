"""B3: confirmations count only when signed with the confirmer's key."""

import json
from datetime import UTC, datetime

import pytest
from support import CONFIRMER, PASSPHRASE, fixture_extracts, plan_for, signer
from typer.testing import CliRunner

from recon_engine import cli
from recon_engine.cli import app
from recon_engine.models import CertificateStatus
from recon_engine.render import render_html
from recon_engine.session import Session, Workspace
from recon_engine.signing import (
    PRIVATE_FILE,
    Signer,
    SigningError,
    init_keys,
    keys_dir,
    load_signer,
    load_trusted_key,
)

CODE = "K7Q2PX"
runner = CliRunner()


def recon(home, *args, input=None):
    return runner.invoke(app, ["--home", str(home), *args], input=input)


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_confirmation_code", lambda: CODE)


@pytest.fixture
def session(tmp_path):
    manifest, a, b, spec = fixture_extracts("clean")
    session = Session(Workspace(tmp_path), a, b, spec)
    for name, params in plan_for("clean", manifest):
        session.run_check(name, params)
    return session


def store_file(session):
    return session.workspace.root / "assumptions.json"


def hand_edit(session, change):
    path = store_file(session)
    records = json.loads(path.read_text(encoding="utf-8"))
    for record in records:
        change(record)
    path.write_text(json.dumps(records), encoding="utf-8")


def test_hand_editing_confirmed_true_into_the_store_counts_for_nothing(session):
    def forge(record):
        record.update(confirmed=True, confirmed_by=CONFIRMER, confirmed_at="2026-11-13T09:00:00Z")

    hand_edit(session, forge)
    cert = session.certify()

    assert cert.status == CertificateStatus.PROVISIONAL
    assert set(cert.unconfirmed_assumptions) == {a.id for a in cert.assumptions}
    assert set(cert.unverified_confirmations.values()) == {"the confirmation is not signed"}
    assert "Confirmation could not be verified" in render_html(cert)


def test_a_properly_signed_confirmation_gives_reconciled(session, interactive, tmp_path):
    for assumption in session.workspace.assumptions.all():
        done = recon(tmp_path, "confirm", assumption.id, input=f"{CODE}\n{PASSPHRASE}\n")
        assert done.exit_code == 0, done.output
    cert = session.certify()

    assert cert.status == CertificateStatus.RECONCILED
    assert cert.unverified_confirmations == {}
    assert cert.confirmer == CONFIRMER
    assert cert.confirmer_key_fingerprint == load_trusted_key().fingerprint
    assert all(a.confirmed_by == CONFIRMER for a in cert.assumptions)
    assert cert.confirmer_key_fingerprint in render_html(cert)


def test_editing_an_assumption_after_confirming_it_invalidates_the_signature(session):
    for assumption in session.workspace.assumptions.all():
        session.workspace.assumptions.confirm(assumption.id, signer())

    def reword(record):
        record["description"] = record["description"].replace("include", "may include")

    hand_edit(session, reword)
    cert = session.certify()

    assert cert.status == CertificateStatus.PROVISIONAL
    assert (
        "the assumption was changed after it was confirmed"
        in cert.unverified_confirmations.values()
    )


def test_copying_a_signature_onto_another_assumption_fails(session):
    store = session.workspace.assumptions
    first, second = store.all()[:2]
    store.confirm(first.id, signer())
    signed = store.get(first.id)

    def borrow(record):
        if record["id"] == second.id:
            for field in ("confirmed", "confirmed_by", "confirmed_at", "confirmed_content_hash",
                          "signature", "signing_key_fingerprint"):  # fmt: skip
                record[field] = json.loads(signed.model_dump_json())[field]

    hand_edit(session, borrow)
    cert = session.certify()

    assert second.id in cert.unverified_confirmations
    assert first.id not in cert.unverified_confirmations


def test_a_confirmation_signed_with_another_key_is_not_trusted(session):
    impostor = Signer.generate(CONFIRMER)  # same name, different key
    for assumption in session.workspace.assumptions.all():
        session.workspace.assumptions.confirm(assumption.id, impostor)
    cert = session.certify()

    assert cert.status == CertificateStatus.PROVISIONAL
    assert all(
        "not this machine's confirmer key" in p for p in cert.unverified_confirmations.values()
    )


def test_without_a_confirmer_key_nothing_counts_as_confirmed(session, monkeypatch, tmp_path):
    for assumption in session.workspace.assumptions.all():
        session.workspace.assumptions.confirm(assumption.id, signer())
    monkeypatch.setenv("RECON_KEYS_DIR", str(tmp_path / "no-keys"))

    assert session.certify().status == CertificateStatus.PROVISIONAL


def test_confirm_with_the_wrong_passphrase_confirms_nothing(session, interactive, tmp_path):
    target = session.workspace.assumptions.all()[0].id
    result = recon(tmp_path, "confirm", target, input=f"{CODE}\nwrong passphrase\n")

    assert result.exit_code == 1
    assert "Wrong passphrase" in result.output
    assert session.workspace.assumptions.get(target).confirmed is False


def test_confirm_needs_a_key(session, interactive, tmp_path, monkeypatch):
    monkeypatch.setenv("RECON_KEYS_DIR", str(tmp_path / "no-keys"))
    target = session.workspace.assumptions.all()[0].id
    result = recon(tmp_path, "confirm", target, input=f"{CODE}\n{PASSPHRASE}\n")

    assert result.exit_code == 1
    assert "recon keys init" in result.output


def test_reconfirming_repairs_a_hand_edited_confirmation(session, interactive, tmp_path):
    def forge(record):
        record.update(confirmed=True, confirmed_by="Someone", confirmed_at="2026-11-13T09:00:00Z")

    hand_edit(session, forge)
    target = session.workspace.assumptions.all()[0].id
    result = recon(tmp_path, "confirm", target, input=f"{CODE}\n{PASSPHRASE}\n")

    assert result.exit_code == 0, result.output
    assert "the confirmation is not signed" in result.output
    assert session.certify().unverified_confirmations.get(target) is None


def test_keys_init_creates_an_encrypted_key(tmp_path, monkeypatch, interactive):
    monkeypatch.setenv("RECON_KEYS_DIR", str(tmp_path / "keys"))
    made = recon(
        tmp_path, "keys", "init", "--name", "Sam Auditor", input="s3cret phrase\ns3cret phrase\n"
    )

    assert made.exit_code == 0, made.output
    key = load_trusted_key()
    assert key.owner == "Sam Auditor"
    assert key.fingerprint in made.output
    assert b"ENCRYPTED PRIVATE KEY" in (tmp_path / "keys" / PRIVATE_FILE).read_bytes()
    with pytest.raises(SigningError, match="Wrong passphrase"):
        load_signer("not it")
    assert load_signer("s3cret phrase").owner == "Sam Auditor"


def test_keys_init_refuses_non_interactive_input(tmp_path, monkeypatch):
    monkeypatch.setenv("RECON_KEYS_DIR", str(tmp_path / "keys"))
    result = recon(tmp_path, "keys", "init", "--name", "X", input="s3cret phrase\ns3cret phrase\n")

    assert result.exit_code == 1
    assert "interactive terminal" in result.output
    assert load_trusted_key() is None


def test_keys_init_never_overwrites_a_key(tmp_path):
    init_keys("First", "passphrase one", tmp_path)
    with pytest.raises(SigningError, match="already exists"):
        init_keys("Second", "passphrase two", tmp_path)


def test_short_passphrases_are_refused(tmp_path):
    with pytest.raises(SigningError, match="at least 8"):
        init_keys("X", "short", tmp_path)


def test_keys_live_in_the_user_profile_not_the_workspace(monkeypatch, tmp_path):
    monkeypatch.delenv("RECON_KEYS_DIR")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr("sys.platform", "win32")

    assert keys_dir() == tmp_path / "Roaming" / "recon" / "keys"


def test_the_signature_covers_the_confirmation_time(session):
    store = session.workspace.assumptions
    target = store.all()[0].id
    store.confirm(target, signer(), at=datetime(2026, 11, 13, 9, 0, tzinfo=UTC))

    def backdate(record):
        if record["id"] == target:
            record["confirmed_at"] = "2026-01-01T00:00:00Z"

    hand_edit(session, backdate)
    assert session.certify().unverified_confirmations[target] == "the signature does not match"
