"""`tieout demo`: both fixture pairs end to end in a temp folder, with a throwaway key."""

import os
from decimal import Decimal

from support import CLEAN_PLAN
from typer.testing import CliRunner

from tieout_ai import cli
from tieout_ai.cli import app
from tieout_ai.demo import run_demo
from tieout_ai.fixtures.generate import PLAN as PACKAGED_CLEAN_PLAN
from tieout_ai.session import Workspace


def test_the_packaged_clean_plan_is_the_one_the_tests_use():
    assert [list(step) for step in CLEAN_PLAN.values()] == PACKAGED_CLEAN_PLAN


def test_demo_reconciles_clean_and_leaves_messy_honestly_open(tmp_path):
    results = {r.case: r for r in run_demo(tmp_path)}
    clean, messy = results["clean"], results["messy"]

    assert clean.status == "RECONCILED"
    assert clean.signed_off is True
    assert messy.status == "OPEN"
    assert messy.residual == Decimal("1326.00")
    for result in (clean, messy):
        assert result.html.is_file()
        cert = Workspace(result.workspace).load_certificate(result.certificate_id)
        assert cert.status.value == result.status


def test_demo_certificates_say_they_were_signed_with_a_throwaway_key(tmp_path):
    clean = next(r for r in run_demo(tmp_path) if r.case == "clean")
    html = clean.html.read_text(encoding="utf-8")

    assert "Signed off by Demo confirmer (throwaway key)" in html
    assert "Expected confirmer key (Demo confirmer (throwaway key))" in html


def test_demo_never_touches_the_real_confirmer_key(tmp_path, confirmer_keys):
    before = {p.name: p.read_bytes() for p in confirmer_keys.iterdir()}
    env = os.environ.get("RECON_KEYS_DIR")
    run_demo(tmp_path / "demo")

    assert os.environ.get("RECON_KEYS_DIR") == env
    assert {p.name: p.read_bytes() for p in confirmer_keys.iterdir()} == before
    assert (tmp_path / "demo" / "keys").is_dir()


def test_cli_demo_prints_results_and_opens_both_certificates(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    result = CliRunner().invoke(app, ["demo", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "clean" in result.output and "RECONCILED, signed off" in result.output
    assert "messy" in result.output and "OPEN" in result.output
    assert len(opened) == 2
    assert all(url.startswith("file:") and url.endswith(".html") for url in opened)


def test_cli_demo_can_skip_opening(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    result = CliRunner().invoke(app, ["demo", "--out", str(tmp_path), "--no-open"])

    assert result.exit_code == 0, result.output
    assert opened == []
