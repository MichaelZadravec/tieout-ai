"""Hardening (2): connections are signed by the person who registers them.

Red-team attacks 3 and 9 wrote connections.json directly: re-registering an
edited file with its new hash, and adding a SQL source no person approved. The
engine now refuses any connection whose record is not signed with this
machine's confirmer key.
"""

import json
import shutil

import pytest
from support import CONFIRMER, FIXTURE_DATA, PASSPHRASE
from typer.testing import CliRunner

from tieout_ai import cli
from tieout_ai.cli import app
from tieout_ai.connections import file_registration, sign_connection
from tieout_ai.models import Connection, ConnectionType
from tieout_ai.service import ReconService, ServiceError
from tieout_ai.session import Workspace
from tieout_ai.signing import Signer

CODE = "K7Q2PX"
runner = CliRunner()


def recon(home, *args, input=None):
    return runner.invoke(app, ["--home", str(home), *args], input=input)


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_confirmation_code", lambda: CODE)


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "dashboard.csv"
    shutil.copy(FIXTURE_DATA / "dashboard_oct2026.csv", path)
    return path


def stored(home):
    return json.loads((home / "connections.json").read_text(encoding="utf-8"))


def write(home, items):
    (home / "connections.json").write_text(json.dumps(items), encoding="utf-8")


def test_add_file_signs_the_registration(tmp_path, csv_file, interactive):
    home = tmp_path / ".tieout"
    added = recon(
        home, "connections", "add-file", "dashboard", str(csv_file), input=f"{PASSPHRASE}\n"
    )
    [item] = stored(home)

    assert added.exit_code == 0, added.output
    assert item["config"]["signature"]["by"] == CONFIRMER
    assert item["config"]["registered"]["registered_by"] == CONFIRMER
    assert ReconService(Workspace(home)).run_query("dashboard")["source_warning"] is None


def test_add_file_needs_a_terminal(tmp_path, csv_file):
    home = tmp_path / ".tieout"
    result = recon(
        home, "connections", "add-file", "dashboard", str(csv_file), input=f"{PASSPHRASE}\n"
    )

    assert result.exit_code == 1
    assert "interactive terminal" in result.output
    assert not (home / "connections.json").exists()


def test_add_file_with_the_wrong_passphrase_registers_nothing(tmp_path, csv_file, interactive):
    home = tmp_path / ".tieout"
    result = recon(home, "connections", "add-file", "dashboard", str(csv_file), input="nope nope\n")

    assert result.exit_code == 1
    assert "Wrong passphrase" in result.output
    assert not (home / "connections.json").exists()


def test_an_unsigned_connection_is_refused(tmp_path):
    workspace = Workspace(tmp_path)
    path = FIXTURE_DATA / "dashboard_oct2026.csv"
    workspace.connections.add(
        Connection(id="dashboard", type=ConnectionType.FILE, config={"path": str(path)})
    )
    with pytest.raises(ServiceError, match="Connection 'dashboard' is not signed"):
        ReconService(workspace).run_query("dashboard")


def test_rewriting_a_registered_hash_breaks_the_signature(tmp_path, csv_file, interactive):
    """Attack 3: edit the file, then write its new hash into connections.json."""
    home = tmp_path / ".tieout"
    recon(home, "connections", "add-file", "dashboard", str(csv_file), input=f"{PASSPHRASE}\n")
    with csv_file.open("a", encoding="utf-8") as fh:
        fh.write("INV-999,2026-10-05,1000.00,PAID\n")
    items = stored(home)
    items[0]["config"]["registered"].update(file_registration(csv_file))
    write(home, items)

    with pytest.raises(ServiceError, match="changed after a person signed it"):
        ReconService(Workspace(home)).run_query("dashboard")


def test_a_hand_added_sql_connection_is_refused(tmp_path, invoices_db):
    """Attack 9: register a database by writing connections.json."""
    home = tmp_path / ".tieout"
    home.mkdir()
    write(home, [{"id": "ledger", "type": "sql", "config": {"url": f"sqlite:///{invoices_db}"}}])

    with pytest.raises(ServiceError, match="Connection 'ledger' is not signed"):
        ReconService(Workspace(home)).run_query("ledger", "SELECT * FROM invoices")


def test_a_connection_signed_with_another_key_is_refused(tmp_path):
    workspace = Workspace(tmp_path)
    path = FIXTURE_DATA / "dashboard_oct2026.csv"
    conn = Connection(
        id="dashboard",
        type=ConnectionType.FILE,
        config={"path": str(path), "registered": file_registration(path)},
    )
    workspace.connections.add(sign_connection(conn, Signer.generate(CONFIRMER)))

    with pytest.raises(ServiceError, match="not this machine's confirmer key"):
        ReconService(workspace).run_query("dashboard")


def test_refresh_signs_the_new_registration(tmp_path, csv_file, interactive):
    home = tmp_path / ".tieout"
    recon(home, "connections", "add-file", "dashboard", str(csv_file), input=f"{PASSPHRASE}\n")
    with csv_file.open("a", encoding="utf-8") as fh:
        fh.write("\n")
    assert ReconService(Workspace(home)).run_query("dashboard")["changed_since_registration"]

    refreshed = recon(home, "connections", "refresh", "dashboard", input=f"{CODE}\n{PASSPHRASE}\n")
    extract = ReconService(Workspace(home)).run_query("dashboard")

    assert refreshed.exit_code == 0, refreshed.output
    assert extract["source_warning"] is None
    assert stored(home)[0]["config"]["registered"]["refreshed_by"] == CONFIRMER


def test_add_sql_signs_the_connection(tmp_path, invoices_db, interactive):
    home = tmp_path / ".tieout"
    url = f"sqlite:///{invoices_db}"
    added = recon(home, "connections", "add-sql", "ledger", "--url", url, input=f"{PASSPHRASE}\n")

    assert added.exit_code == 0, added.output
    assert stored(home)[0]["config"]["signature"]["by"] == CONFIRMER
    assert (
        ReconService(Workspace(home)).run_query("ledger", "SELECT * FROM invoices")["row_count"]
        == 5
    )


def test_list_connections_says_which_are_signed(tmp_path, csv_file, interactive):
    home = tmp_path / ".tieout"
    recon(home, "connections", "add-file", "dashboard", str(csv_file), input=f"{PASSPHRASE}\n")
    items = stored(home)
    items.append({"id": "rogue", "type": "file", "config": {"path": str(csv_file)}})
    write(home, items)

    listed = {c["id"]: c for c in ReconService(Workspace(home)).list_connections()}
    shown = recon(home, "connections", "list")

    assert listed["dashboard"]["signed"] is True
    assert listed["rogue"]["signed"] is False
    assert "NOT SIGNED" in shown.output
