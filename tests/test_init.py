"""B4: `recon init` turns an empty folder into a working, locked-down trial folder."""

import json

import pytest
from support import CONFIRMER
from typer.testing import CliRunner

from recon_engine import cli
from recon_engine.cli import app
from recon_engine.project import DENIED_COMMANDS, init_folder
from recon_engine.signing import load_trusted_key

runner = CliRunner()


def files_of(folder):
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_init_writes_the_workspace_mcp_config_and_settings(tmp_path):
    init_folder(tmp_path)

    assert (tmp_path / ".recon").is_dir()
    mcp = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp == {"mcpServers": {"recon": {"command": "recon-mcp", "args": ["--home", ".recon"]}}}
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == ["mcp__recon__*"]
    deny = settings["permissions"]["deny"]
    for command in ("recon confirm", "recon keys", "recon connections"):
        for tool in ("Bash", "PowerShell"):
            assert f"{tool}({command}*)" in deny
            assert f"{tool}(*{command}*)" in deny
    assert "Edit(.recon/**)" in deny
    assert "Edit(/.recon/**)" in deny


def test_denied_commands_cover_every_human_only_action():
    assert set(DENIED_COMMANDS) == {"confirm", "keys", "connections", "cert sign"}


def test_running_init_twice_changes_nothing(tmp_path):
    first = init_folder(tmp_path)
    before = files_of(tmp_path)
    second = init_folder(tmp_path)

    assert {status for _, status in first} == {"created"}
    assert {status for _, status in second} == {"unchanged"}
    assert files_of(tmp_path) == before


def test_init_keeps_whatever_the_folder_already_has(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"model": "x", "permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(rm*)"]}}),
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-mcp"}}}), encoding="utf-8"
    )
    init_folder(tmp_path)

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    mcp = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert settings["model"] == "x"
    assert settings["permissions"]["allow"] == ["Bash(ls)", "mcp__recon__*"]
    assert settings["permissions"]["deny"][0] == "Bash(rm*)"
    assert "Bash(recon confirm*)" in settings["permissions"]["deny"]
    assert set(mcp["mcpServers"]) == {"other", "recon"}


def test_init_leaves_an_existing_workspace_alone(tmp_path):
    (tmp_path / ".recon").mkdir()
    (tmp_path / ".recon" / "connections.json").write_text("[]\n", encoding="utf-8")
    init_folder(tmp_path)

    assert (tmp_path / ".recon" / "connections.json").read_text(encoding="utf-8") == "[]\n"


def recon_init(folder, input=None):
    return runner.invoke(app, ["init", str(folder)], input=input)


def test_cli_init_reports_path_problems_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    result = recon_init(tmp_path)

    assert result.exit_code == 0, result.output
    assert "not on your PATH" in result.output
    assert "uv tool update-shell" in result.output
    assert "open a new terminal" in result.output


def test_cli_init_prints_next_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"C:/bin/{name}.exe")
    result = recon_init(tmp_path)

    assert "Next steps" in result.output
    assert "recon connections add-file" in result.output
    assert "Open Claude Code in" in result.output
    assert "recon confirm" in result.output
    assert f"Confirmer key: {CONFIRMER}" in result.output  # the test key already exists


def test_cli_init_creates_a_key_when_there_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("RECON_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    result = recon_init(tmp_path / "trial", input="Sam Auditor\ns3cret phrase\ns3cret phrase\n")

    assert result.exit_code == 0, result.output
    assert load_trusted_key().owner == "Sam Auditor"


def test_cli_init_without_a_terminal_explains_how_to_make_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("RECON_KEYS_DIR", str(tmp_path / "keys"))
    result = recon_init(tmp_path / "trial")

    assert result.exit_code == 0, result.output
    assert 'recon keys init --name "Your Name"' in result.output
    assert load_trusted_key() is None


@pytest.mark.parametrize("run", [1, 2])
def test_the_folder_init_makes_runs_the_server(tmp_path, run):
    from support import FIXTURE_DATA, registered_file

    from recon_engine.mcp_server import build_server
    from recon_engine.service import ReconService
    from recon_engine.session import Workspace

    for _ in range(run):
        init_folder(tmp_path)
    workspace = Workspace(tmp_path / ".recon")
    workspace.connections.add(registered_file("dashboard", FIXTURE_DATA / "dashboard_oct2026.csv"))

    assert build_server(workspace) is not None
    assert [c["id"] for c in ReconService(workspace).list_connections()] == ["dashboard"]
