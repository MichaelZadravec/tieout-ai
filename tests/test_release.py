"""Release metadata: licence, names and entry points."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_the_licence_is_apache_2():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "Apache License" in text.splitlines()[1]
    assert "Version 2.0, January 2004" in text
    assert "END OF TERMS AND CONDITIONS" in text
    assert PROJECT["license"] == "Apache-2.0"
    assert PROJECT["license-files"] == ["LICENSE"]


def test_names_and_entry_points():
    scripts = PROJECT["scripts"]

    assert PROJECT["name"] == "tieout-ai"
    assert scripts["tieout"] == scripts["tieout-ai"] == "tieout_ai.cli:app"
    assert scripts["tieout-mcp"] == "tieout_ai.mcp_server:main"
    assert scripts["recon"] == "tieout_ai.cli:app"  # alias, for now


def test_no_home_directory_paths_ship_in_the_repo():
    """Personal paths (a Windows or macOS home folder) must not leak into the release."""
    import re

    home = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+\w|/Users/\w|/home/\w")
    assert home.search(r"C:\Users\someone") and home.search("/Users/someone")
    roots = [
        ROOT / "src",
        ROOT / "tests",
        ROOT / "fixtures",
        *ROOT.glob("*.md"),
        ROOT / "pyproject.toml",
    ]
    offenders = []
    for root in roots:
        for path in [root] if root.is_file() else root.rglob("*"):
            text_file = path.suffix in {".py", ".md", ".json", ".csv", ".toml", ".yml"}
            if (
                path.is_file()
                and text_file
                and path.name != "test_release.py"
                and home.search(path.read_text(encoding="utf-8", errors="ignore"))
            ):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
