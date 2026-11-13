"""Hardening (1): a certificate must be self-consistent to load, even if its hash checks out.

The hash only proves the file was not changed after it was hashed; anyone can
recompute it. These tests forge certificates with a correct hash and check that
loading still refuses them whenever their content contradicts itself.
"""

import json
from pathlib import Path

import pytest
from support import fixture_extracts, plan_for, signer

from recon_engine.certify import _UNHASHED
from recon_engine.hashing import canonical_json, sha256_hex
from recon_engine.session import CertificateError, Session, Workspace

LEGACY = Path(__file__).parent / "data" / "certificate_v0_pre_4b.json"


def issue(home, kind="messy", confirm=False):
    manifest, a, b, spec = fixture_extracts(kind)
    session = Session(Workspace(home), a, b, spec)
    for name, params in plan_for(kind, manifest):
        session.run_check(name, params)
    if confirm:
        for assumption in session.workspace.assumptions.all():
            session.workspace.assumptions.confirm(assumption.id, signer())
    return session.certify()


def document(home, cert_id):
    return json.loads((home / "certificates" / f"{cert_id}.json").read_text(encoding="utf-8"))


def forge(home, doc, **changes):
    """Write a changed certificate whose hash and id are recomputed, as a forger would."""
    forged = {**doc, **changes}
    digest = sha256_hex(canonical_json({k: v for k, v in forged.items() if k not in _UNHASHED}))
    forged.update(id=f"cert_{digest[:16]}", certificate_hash=digest)
    (home / "certificates" / f"{forged['id']}.json").write_text(
        json.dumps(forged), encoding="utf-8"
    )
    return forged["id"]


def refused(home, cert_id, message):
    with pytest.raises(CertificateError, match=message):
        Workspace(home).load_certificate(cert_id)


def test_genuine_certificates_load(tmp_path):
    for kind, confirm in (("messy", False), ("clean", False), ("clean", True)):
        home = tmp_path / f"{kind}-{confirm}"
        cert = issue(home, kind, confirm)
        assert Workspace(home).load_certificate(cert.id) == cert


def test_a_forged_status_is_refused(tmp_path):
    cert = issue(tmp_path)  # OPEN: items are unexplained
    forged = forge(tmp_path, document(tmp_path, cert.id), status="RECONCILED")
    refused(tmp_path, forged, "status RECONCILED does not follow")


def test_dropping_the_unexplained_items_is_refused(tmp_path):
    cert = issue(tmp_path)
    doc = document(tmp_path, cert.id)
    forged = forge(
        tmp_path, doc, status="RECONCILED", residual="0.00", gross_unexplained="0.00",
        residual_items=[], unconfirmed_assumptions=[],
    )  # fmt: skip
    refused(tmp_path, forged, "explained .* plus residual .* does not equal the gap")


def test_inflating_explained_is_refused(tmp_path):
    cert = issue(tmp_path)
    doc = document(tmp_path, cert.id)
    forged = forge(tmp_path, doc, explained=doc["match"]["gap"], residual="0.00")
    refused(tmp_path, forged, "explained .* is not the sum of the buckets")


def test_a_wrong_gross_is_refused(tmp_path):
    cert = issue(tmp_path)
    forged = forge(tmp_path, document(tmp_path, cert.id), gross_unexplained="1.00")
    refused(tmp_path, forged, "gross unexplained")


def test_an_item_both_explained_and_unexplained_is_refused(tmp_path):
    cert = issue(tmp_path)
    doc = document(tmp_path, cert.id)
    item = doc["residual_items"][0]
    bucket = {**doc["buckets"][0], "item_ids": [*doc["buckets"][0]["item_ids"], item["id"]]}
    forged = forge(tmp_path, doc, buckets=[bucket, *doc["buckets"][1:]])
    refused(tmp_path, forged, f"item {item['id']} is both explained and unexplained")


def test_hiding_unconfirmed_assumptions_is_refused(tmp_path):
    cert = issue(tmp_path, "clean")  # PROVISIONAL
    forged = forge(
        tmp_path, document(tmp_path, cert.id), status="RECONCILED", unconfirmed_assumptions=[]
    )
    refused(tmp_path, forged, "is not confirmed but not listed as unconfirmed")


def test_claiming_confirmations_without_signatures_is_refused(tmp_path):
    cert = issue(tmp_path, "clean")
    doc = document(tmp_path, cert.id)
    confirmed = [
        {**a, "confirmed": True, "confirmed_by": "Jane CFO", "confirmed_at": "2026-11-13T09:00:00Z"}
        for a in doc["assumptions"]
    ]
    forged = forge(
        tmp_path, doc, status="RECONCILED", assumptions=confirmed, unconfirmed_assumptions=[],
        confirmer="Jane CFO", confirmer_key_fingerprint=signer().trusted.fingerprint,
    )  # fmt: skip
    refused(tmp_path, forged, "confirmation of .* is not signed")


def test_tampered_signatures_are_refused(tmp_path):
    cert = issue(tmp_path, "clean", confirm=True)
    doc = document(tmp_path, cert.id)
    reworded = [{**a, "description": a["description"] + " (edited)"} for a in doc["assumptions"]]
    forged = forge(tmp_path, doc, assumptions=reworded)
    refused(tmp_path, forged, "confirmation of .* does not verify")


def test_a_bucket_citing_an_unlisted_assumption_is_refused(tmp_path):
    cert = issue(tmp_path)
    doc = document(tmp_path, cert.id)
    bucket = {**doc["buckets"][0], "assumption_ids": ["asm_000000000000"]}
    forged = forge(tmp_path, doc, buckets=[bucket, *doc["buckets"][1:]])
    refused(tmp_path, forged, "cites assumption asm_000000000000")


def test_the_earlier_certificate_still_loads_but_says_its_confirmations_are_unsigned(tmp_path):
    from recon_engine.certify import confirmation_status

    workspace = Workspace(tmp_path)
    workspace.certificate_dir.mkdir(parents=True)
    doc = json.loads(LEGACY.read_text(encoding="utf-8"))
    (workspace.certificate_dir / f"{doc['id']}.json").write_text(
        LEGACY.read_text(encoding="utf-8"), encoding="utf-8"
    )
    cert = workspace.load_certificate(doc["id"])

    assert confirmation_status(cert) == (
        "issued before confirmations were signed: its confirmations cannot be verified"
    )


def test_a_signed_certificate_reports_verified_confirmations(tmp_path):
    from recon_engine.certify import confirmation_status

    cert = issue(tmp_path, "clean", confirm=True)
    loaded = Workspace(tmp_path).load_certificate(cert.id)

    assert confirmation_status(loaded, signer().trusted) is None
    assert "not this machine's confirmer key" in confirmation_status(loaded, None)


def test_get_certificate_and_cert_show_report_unverifiable_confirmations(tmp_path):
    from typer.testing import CliRunner

    from recon_engine.cli import app
    from recon_engine.service import ReconService

    workspace = Workspace(tmp_path)
    workspace.certificate_dir.mkdir(parents=True)
    doc = json.loads(LEGACY.read_text(encoding="utf-8"))
    (workspace.certificate_dir / f"{doc['id']}.json").write_text(
        LEGACY.read_text(encoding="utf-8"), encoding="utf-8"
    )

    summary = ReconService(workspace).get_certificate(doc["id"])
    shown = CliRunner().invoke(app, ["--home", str(tmp_path), "cert", "show", doc["id"]])

    assert summary["confirmations_verified"] is False
    assert "cannot be verified" in summary["confirmation_warning"]
    assert "cannot be verified" in shown.output
