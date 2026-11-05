import json

from support import FIXTURE_DATA
from typer.testing import CliRunner

from recon_engine.cli import app
from recon_engine.connections import ConnectionStore
from recon_engine.models import Connection, ConnectionType

runner = CliRunner()


def recon(home, *args, input=None):
    return runner.invoke(app, ["--home", str(home), *args], input=input)


def test_store_persists_and_reloads(tmp_path):
    path = tmp_path / "connections.json"
    ConnectionStore(path).add(
        Connection(id="erp", type=ConnectionType.SQL, config={"url": "sqlite:///x.db"})
    )

    reloaded = ConnectionStore(path).get("erp")
    assert reloaded.type == ConnectionType.SQL
    assert reloaded.config == {"url": "sqlite:///x.db"}


def test_add_file_then_list(tmp_path):
    home = tmp_path / "home"
    added = recon(home, "connections", "add-file", "dashboard", str(FIXTURE_DATA / "manifest.json"))
    listed = recon(home, "connections", "list")

    assert added.exit_code == 0, added.output
    assert "dashboard  [file]" in listed.output


def test_add_file_requires_an_existing_file(tmp_path):
    result = recon(tmp_path / "home", "connections", "add-file", "x", str(tmp_path / "nope.csv"))
    assert result.exit_code == 1
    assert "No file at" in result.output


def test_sql_passwords_are_never_shown(tmp_path):
    home = tmp_path / "home"
    url = "postgresql://reader:s3cret@db.example/finance"
    added = recon(home, "connections", "add-sql", "erp", "--url", url)
    listed = recon(home, "connections", "list")

    assert added.exit_code == 0, added.output
    assert "s3cret" not in added.output
    assert "s3cret" not in listed.output
    assert "reader:***@db.example/finance" in listed.output
    stored = json.loads((home / "connections.json").read_text(encoding="utf-8"))
    assert stored[0]["config"]["url"] == url  # kept locally, for the engine only


def test_sql_url_can_be_entered_at_a_hidden_prompt(tmp_path):
    home = tmp_path / "home"
    result = recon(home, "connections", "add-sql", "erp", input="sqlite:///ledger.db\n")

    assert result.exit_code == 0, result.output
    assert ConnectionStore(home / "connections.json").get("erp").config["url"].endswith("ledger.db")


def test_duplicate_ids_need_replace(tmp_path):
    home = tmp_path / "home"
    path = str(FIXTURE_DATA / "manifest.json")
    recon(home, "connections", "add-file", "x", path)

    assert recon(home, "connections", "add-file", "x", path).exit_code == 1
    assert recon(home, "connections", "add-file", "x", path, "--replace").exit_code == 0


def test_remove(tmp_path):
    home = tmp_path / "home"
    recon(home, "connections", "add-file", "x", str(FIXTURE_DATA / "manifest.json"))

    assert recon(home, "connections", "remove", "x").exit_code == 0
    assert "No connections yet" in recon(home, "connections", "list").output
