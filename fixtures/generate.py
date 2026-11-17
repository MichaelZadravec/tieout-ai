"""Regenerate the committed fixtures. The generator lives in tieout_ai.fixtures.generate.

Usage: uv run python fixtures/generate.py [OUT_DIR]   (default: fixtures/data)
"""

from pathlib import Path

from tieout_ai.fixtures.generate import *  # noqa: F403  (tests import names from here)
from tieout_ai.fixtures.generate import main

if __name__ == "__main__":
    main(Path(__file__).parent / "data")
