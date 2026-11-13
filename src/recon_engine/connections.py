"""The connection store: data sources the engine may read, with their credentials.

Connections are added by a person through the CLI and live only on this machine
(`connections.json` in the workspace). The MCP server can list them by ID and
type and fetch through them, but never returns their config, so an AI never
sees a path, URL or password.

The file on disk is the only source of truth: every call reads it afresh, so a
connection added in a terminal is visible to a running MCP server at once.

Every connection is signed by the person who registers it (`sign_connection`):
the engine reads only through connections whose whole record verifies against
the confirmer key, so an edit to `connections.json` cannot add or alter one.

A file connection records the file's hash, size and modified time when a person
registers it (`file_registration`). Every read re-hashes the file, so an edit
made after registration, by anyone, is caught.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from recon_engine.hashing import hash_file
from recon_engine.models import Connection, ConnectionType
from recon_engine.signing import Signer, TrustedKey


class ConnectionStoreError(Exception):
    """A connection is unknown or invalid."""


def file_registration(path: Path) -> dict[str, Any]:
    """What a person vouches for when registering a file: its exact bytes."""
    stat = path.stat()
    return {
        "sha256": hash_file(path),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def _unsigned(connection: Connection) -> dict[str, Any]:
    config = {k: v for k, v in connection.config.items() if k != "signature"}
    return {"id": connection.id, "type": connection.type.value, "config": config}


def sign_connection(
    connection: Connection, signer: Signer, at: datetime | None = None
) -> Connection:
    """The connection, signed by the person registering it: its ID, type and whole config."""
    signature = signer.sign_record("connection", _unsigned(connection), at)
    config = {**_unsigned(connection)["config"], "signature": signature}
    return connection.model_copy(update={"config": config})


def connection_problem(connection: Connection, key: TrustedKey | None) -> str | None:
    """Why the engine must not read through this connection, or None if it may."""
    if key is None:
        return "cannot be checked: there is no confirmer key on this machine"
    return key.record_problem(
        "connection", _unsigned(connection), connection.config.get("signature")
    )


class ConnectionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Connection]:
        if not self.path.exists():
            return {}
        items = {}
        for raw in json.loads(self.path.read_text(encoding="utf-8")):
            conn = Connection(id=raw["id"], type=ConnectionType(raw["type"]), config=raw["config"])
            items[conn.id] = conn
        return items

    def add(self, connection: Connection, *, replace: bool = False) -> Connection:
        if not connection.id.strip():
            raise ConnectionStoreError("A connection needs an ID")
        items = self._load()
        if connection.id in items and not replace:
            raise ConnectionStoreError(f"Connection '{connection.id}' already exists")
        items[connection.id] = connection
        self._save(items)
        return connection

    def remove(self, connection_id: str) -> None:
        items = self._load()
        self._get(items, connection_id)
        del items[connection_id]
        self._save(items)

    def get(self, connection_id: str) -> Connection:
        return self._get(self._load(), connection_id)

    @staticmethod
    def _get(items: dict[str, Connection], connection_id: str) -> Connection:
        try:
            return items[connection_id]
        except KeyError:
            raise ConnectionStoreError(
                f"No connection '{connection_id}'; available: {sorted(items)}"
            ) from None

    def all(self) -> list[Connection]:
        items = self._load()
        return [items[i] for i in sorted(items)]

    def _save(self, items: dict[str, Connection]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {"id": c.id, "type": c.type.value, "config": c.config}
            for c in (items[i] for i in sorted(items))
        ]
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".connections-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
