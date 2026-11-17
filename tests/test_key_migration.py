"""The confirmer key moves once from ~/.recon/keys (the earlier name) to ~/.tieout/keys."""

import pytest
from typer.testing import CliRunner

from tieout_ai.cli import app
from tieout_ai.signing import (
    OWNER_FILE,
    PRIVATE_FILE,
    PUBLIC_FILE,
    init_keys,
    keys_dir,
    load_signer,
    load_trusted_key,
    migrate_legacy_keys,
)

KEY_FILES = (PRIVATE_FILE, PUBLIC_FILE, OWNER_FILE)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A user profile with only an old-style key folder, and no override."""
    monkeypatch.delenv("TIEOUT_KEYS_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    init_keys("Jane CFO", "old passphrase", tmp_path / ".recon" / "keys")
    return tmp_path


def test_the_key_folder_is_now_dot_tieout(home):
    assert keys_dir() == home / ".tieout" / "keys"


def test_an_existing_key_is_copied_once_and_still_works(home):
    old = home / ".recon" / "keys"
    before = {name: (old / name).read_bytes() for name in KEY_FILES}

    moved = migrate_legacy_keys()
    key = load_trusted_key()

    assert moved == home / ".tieout" / "keys"
    assert {name: (moved / name).read_bytes() for name in KEY_FILES} == before
    assert key.owner == "Jane CFO"
    assert load_signer("old passphrase").owner == "Jane CFO"
    assert migrate_legacy_keys() is None  # once only
    assert all((old / name).is_file() for name in KEY_FILES)  # the original is left in place


def test_loading_the_key_migrates_it_automatically(home):
    assert load_trusted_key().owner == "Jane CFO"
    assert (home / ".tieout" / "keys" / PRIVATE_FILE).is_file()


def test_an_existing_new_key_is_never_overwritten(home):
    init_keys("Sam Auditor", "new passphrase", home / ".tieout" / "keys")

    assert migrate_legacy_keys() is None
    assert load_trusted_key().owner == "Sam Auditor"


def test_an_override_folder_is_not_migrated_into(home, monkeypatch):
    monkeypatch.setenv("TIEOUT_KEYS_DIR", str(home / "elsewhere"))

    assert migrate_legacy_keys() is None
    assert load_trusted_key() is None
    assert not (home / ".tieout").exists()


def test_the_cli_says_when_it_moved_the_key(home):
    result = CliRunner().invoke(app, ["keys", "fingerprint"])

    assert result.exit_code == 0, result.output
    assert "Copied your confirmer key from" in result.output
    assert ".tieout" in result.output
    again = CliRunner().invoke(app, ["keys", "fingerprint"])
    assert "Copied" not in again.output
