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
