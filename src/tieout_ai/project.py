"""`tieout init`: make a folder ready for an AI to reconcile in, safely.

It creates the workspace (`.tieout/`), registers the MCP server for Claude Code
(`.mcp.json`) and writes Claude Code permissions (`.claude/settings.json`) that
allow the recon tools but deny every human-only command (confirm, keys,
connections) and any edit inside the workspace.

It is idempotent and merges rather than overwrites: other MCP servers and other
permission rules already in the folder are kept, and running it again writes
nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WORKSPACE = ".tieout"
MCP_FILE = ".mcp.json"
SETTINGS_FILE = Path(".claude") / "settings.json"
MCP_NAME = "tieout"
MCP_SERVER = {"command": "tieout-mcp", "args": ["--home", WORKSPACE]}
ALLOW = [f"mcp__{MCP_NAME}__*"]

# Commands only a person may run: confirming, managing keys, registering or
# refreshing sources, and signing certificates off. Denied in every form an
# agent's shell might spell them.
DENIED_COMMANDS = ("confirm", "keys", "connections", "cert sign")
_COMMANDS = ("tieout", "tieout-ai", "recon")  # recon: the earlier name, still installed
_SPELLINGS = (
    *(f"{name} {{c}}*" for name in _COMMANDS),
    *(f"uv run {name} {{c}}*" for name in _COMMANDS),
    "uvx tieout-ai {c}*",
    *(f"*{name} {{c}}*" for name in _COMMANDS),
    *(f"*{name}.exe {{c}}*" for name in _COMMANDS),
    "*tieout_ai.cli {c}*",
)
DENY = [
    f"{tool}({spelling.format(c=command)})"
    for command in DENIED_COMMANDS
    for tool in ("Bash", "PowerShell")
    for spelling in _SPELLINGS
] + [f"Edit(/{WORKSPACE}/**)", f"Edit({WORKSPACE}/**)"]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> str:
    """Write `data` if it differs from what is there; say what happened."""
    text = json.dumps(data, indent=2) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return "unchanged"
        status = "updated"
    else:
        status = "created"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return status


def _merged(existing: list[str], wanted: list[str]) -> list[str]:
    return [*existing, *(rule for rule in wanted if rule not in existing)]


def init_folder(folder: Path) -> list[tuple[str, str]]:
    """Set the folder up; return (path, created/updated/unchanged) for each part."""
    folder.mkdir(parents=True, exist_ok=True)
    results = []

    workspace = folder / WORKSPACE
    results.append((f"{WORKSPACE}/", "unchanged" if workspace.is_dir() else "created"))
    workspace.mkdir(exist_ok=True)

    mcp = _read_json(folder / MCP_FILE)
    servers = mcp.get("mcpServers", {})
    if servers.get(MCP_NAME) != MCP_SERVER:
        mcp = {**mcp, "mcpServers": {**servers, MCP_NAME: MCP_SERVER}}
    results.append((MCP_FILE, _write_json(folder / MCP_FILE, mcp)))

    settings = _read_json(folder / SETTINGS_FILE)
    permissions = settings.get("permissions", {})
    allow = _merged(permissions.get("allow", []), ALLOW)
    deny = _merged(permissions.get("deny", []), DENY)
    if (allow, deny) != (permissions.get("allow"), permissions.get("deny")):
        settings = {**settings, "permissions": {**permissions, "allow": allow, "deny": deny}}
    results.append((SETTINGS_FILE.as_posix(), _write_json(folder / SETTINGS_FILE, settings)))
    return results
