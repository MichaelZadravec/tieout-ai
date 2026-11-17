"""SECURITY.md must stay in step with the code it describes."""

from pathlib import Path

from tieout_ai.project import DENIED_COMMANDS

DOC = Path(__file__).parent.parent / "SECURITY.md"
ATTACKS = [str(n) for n in range(1, 15)] + ["15a", "15b", "15c"]


def text():
    return DOC.read_text(encoding="utf-8")


def test_security_md_exists_with_the_required_sections():
    doc = text()
    for heading in (
        "## Threat model",
        "## What is caught",
        "## What is flagged",
        "## Out of scope",
        "## Red-team results",
        "## Known limitations: future work and out of scope",
        "## Reporting a vulnerability",
    ):
        assert heading in doc, heading


def test_every_human_only_command_is_documented():
    doc = text()
    for command in DENIED_COMMANDS:
        assert f"tieout {command}" in doc, command


def test_every_red_team_attack_has_a_row():
    rows = [line for line in text().splitlines() if line.startswith("| ")]
    numbers = [row.split("|")[1].strip() for row in rows]
    for attack in ATTACKS:
        assert attack in numbers, attack


def test_the_open_attacks_are_called_out_as_future_work():
    section = "## Known limitations: future work and out of scope"
    limits = text().split(section, 1)[1].split("\n## ", 1)[0]
    assert "attack 6" in limits.lower()
    assert "15c" in limits
    assert "future work" in limits.lower()


def test_no_internal_roadmap_names():
    assert "Phase " not in text()


def test_the_fingerprint_check_mentions_a_published_fingerprint(tmp_path):
    from support import fixture_extracts, plan_for, signer
    from typer.testing import CliRunner

    from tieout_ai.cli import app
    from tieout_ai.render import render_html
    from tieout_ai.session import Session, Workspace

    manifest, a, b, spec = fixture_extracts("clean")
    session = Session(Workspace(tmp_path), a, b, spec)
    for name, params in plan_for("clean", manifest):
        session.run_check(name, params)
    for assumption in session.workspace.assumptions.all():
        session.workspace.assumptions.confirm(assumption.id, signer())
    cert = session.certify()
    shown = CliRunner().invoke(app, ["--home", str(tmp_path), "cert", "show", cert.id])

    assert "or against the fingerprint the signer publishes" in render_html(cert)
    assert "or against the fingerprint the signer publishes" in shown.output
    assert "or against the fingerprint the signer publishes" in text()
