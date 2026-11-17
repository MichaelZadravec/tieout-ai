"""B1: file sources are hashed when a person registers them, and re-checked on every read."""

import shutil

import pytest
from support import FIXTURE_DATA, FIXTURE_MATCH_ARGS, PASSPHRASE, plan_for
from typer.testing import CliRunner

from tieout_ai import cli
from tieout_ai.cli import app
from tieout_ai.connections import file_registration
from tieout_ai.connectors.files import FileConnector
from tieout_ai.models import Connection, ConnectionType
from tieout_ai.service import ReconService
from tieout_ai.session import Workspace

CODE = "K7Q2PX"
runner = CliRunner()


def recon(home, *args, input=None):
    return runner.invoke(app, ["--home", str(home), *args], input=input)


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_confirmation_code", lambda: CODE)


@pytest.fixture
def trial(tmp_path, interactive):
    """A workspace whose two sources are copies of the clean fixture, registered by a person."""
    home = tmp_path / ".tieout"
    files = {}
    for conn_id, name in (("dashboard", "dashboard_oct2026.csv"), ("xero", "xero_oct_nov2026.csv")):
        files[conn_id] = tmp_path / name
        shutil.copy(FIXTURE_DATA / name, files[conn_id])
        added = recon(
            home, "connections", "add-file", conn_id, str(files[conn_id]), input=f"{PASSPHRASE}\n"
        )
        assert added.exit_code == 0, added.output
    return home, files


def reconcile(home):
    """Run the clean plan through the service, as the AI would, and certify."""
    service = ReconService(Workspace(home))
    a = service.run_query("dashboard")["extract_id"]
    b = service.run_query("xero")["extract_id"]
    rec = service.match(a, b, **FIXTURE_MATCH_ARGS)["reconciliation_id"]
    for name, params in plan_for("clean", None):
        service.run_check(rec, name, params)
    return service.certify(rec)


def confirm_everything(home):
    for assumption in Workspace(home).assumptions.all():
        done = recon(home, "confirm", assumption.id, input=f"{CODE}\n{PASSPHRASE}\n")
        assert done.exit_code == 0, done.output


def test_add_file_records_hash_size_and_modified_time(trial):
    home, files = trial
    config = Workspace(home).connections.get("dashboard").config
    registered = config["registered"]

    assert registered == {**file_registration(files["dashboard"]), **{
        k: registered[k] for k in ("registered_by", "registered_at")
    }}  # fmt: skip
    assert len(registered["sha256"]) == 64
    assert registered["size"] == files["dashboard"].stat().st_size


def test_an_unchanged_file_reads_cleanly(trial):
    home, _ = trial
    extract = ReconService(Workspace(home)).run_query("dashboard")

    assert extract["changed_since_registration"] is False
    assert extract["source_warning"] is None


def test_an_edited_file_is_flagged_and_the_certificate_is_open(trial, interactive):
    home, files = trial
    assert reconcile(home)["status"] == "PROVISIONAL"
    confirm_everything(home)
    assert reconcile(home)["status"] == "RECONCILED"

    with files["xero"].open("a", encoding="utf-8") as fh:
        fh.write("\n")  # the AI (or anyone) touches the file: same rows, different bytes

    service = ReconService(Workspace(home))
    extract = service.run_query("xero")
    cert = reconcile(home)

    assert extract["changed_since_registration"] is True
    assert cert["status"] == "OPEN"
    assert cert["residual"] == "0.00"  # it still balances; it just cannot be trusted
    [warning] = cert["source_warnings"]
    assert "xero_oct_nov2026.csv" in warning
    assert "changed since it was registered" in warning


def test_refresh_re_registers_and_the_certificate_can_reconcile_again(trial, interactive):
    home, files = trial
    reconcile(home)
    confirm_everything(home)
    with files["xero"].open("a", encoding="utf-8") as fh:
        fh.write("\n")
    assert reconcile(home)["status"] == "OPEN"

    refreshed = recon(home, "connections", "refresh", "xero", input=f"{CODE}\n{PASSPHRASE}\n")
    registered = Workspace(home).connections.get("xero").config["registered"]

    assert refreshed.exit_code == 0, refreshed.output
    assert registered["refreshed_by"] == "Jane CFO"
    assert registered["refreshed_at"]
    assert registered["sha256"] == file_registration(files["xero"])["sha256"]
    assert reconcile(home)["status"] == "RECONCILED"


def test_refresh_refuses_non_interactive_input(trial, monkeypatch):
    home, _ = trial
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    result = recon(home, "connections", "refresh", "xero", input=f"{CODE}\n")

    assert result.exit_code == 1
    assert "interactive terminal" in result.output


def test_refresh_with_the_wrong_code_changes_nothing(trial, interactive):
    home, files = trial
    with files["xero"].open("a", encoding="utf-8") as fh:
        fh.write("\n")
    before = Workspace(home).connections.get("xero").config

    result = recon(home, "connections", "refresh", "xero", input="WRONG\n")

    assert result.exit_code == 1
    assert Workspace(home).connections.get("xero").config == before


def test_a_file_registered_without_a_hash_cannot_reconcile(tmp_path):
    """Connections added before B1 have nothing to check against: never better than OPEN."""
    conn = Connection(
        id="old",
        type=ConnectionType.FILE,
        config={"path": str(FIXTURE_DATA / "dashboard_oct2026.csv")},
    )
    data = FileConnector(conn).fetch()

    assert data.extract.changed_since_registration is False
    assert "not registered with a file hash" in data.extract.source_warning
    assert "tieout connections refresh old" in data.extract.source_warning


def test_cli_and_html_show_the_warning(trial, interactive, tmp_path):
    home, files = trial
    with files["dashboard"].open("a", encoding="utf-8") as fh:
        fh.write("\n")
    cert = reconcile(home)
    shown = recon(home, "cert", "show", cert["certificate_id"])
    html = (home / "certificates" / f"{cert['certificate_id']}.html").read_text(encoding="utf-8")

    assert "dashboard_oct2026.csv" in shown.output
    assert "changed since it was registered" in shown.output
    assert "changed since it was registered" in html
