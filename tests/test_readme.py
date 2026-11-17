"""The README tells the story in the agreed order, and its quickstart is the real one."""

from pathlib import Path

README = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
ORDER = [
    "# AI TieOut",
    "## The problem",
    "## AI proposes, code proves, people confirm",
    "## Try it in 60 seconds",
    "## How it works",
    "## Security",
    "## Why I built this",
]


def section(heading):
    return README.split(heading + "\n", 1)[1].split("\n## ", 1)[0]


def test_sections_come_in_order():
    positions = [README.index(heading + "\n") for heading in ORDER]
    assert positions == sorted(positions)


def test_the_quickstart_is_the_published_command():
    assert "uvx tieout-ai demo" in section("## Try it in 60 seconds")


def test_security_summary_links_to_security_md():
    assert "(SECURITY.md)" in section("## Security")


def test_links_and_licence():
    assert "(CONTRIBUTING.md)" in README
    assert "Apache-2.0" in README
    assert "CLAUDE.md" not in README  # the internal build brief is not user documentation


def test_every_built_in_check_is_described():
    from tieout_ai.checks import list_checks

    for check in list_checks():
        assert f"`{check.name}`" in README, check.name
