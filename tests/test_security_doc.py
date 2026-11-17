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
        "## Still open",
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


def test_the_open_attacks_are_called_out():
    still_open = text().split("## Still open", 1)[1].split("\n## ", 1)[0]
    assert "attack 6" in still_open.lower()
    assert "15c" in still_open
    assert "Phase 6" in still_open
