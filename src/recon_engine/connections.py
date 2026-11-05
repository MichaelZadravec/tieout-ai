"""The connection store: data sources the engine may read, with their credentials.

Connections are added by a person through the CLI and live only on this machine
(`connections.json` in the workspace). The MCP server can list them by ID and
type and fetch through them, but never returns their config, so an AI never
sees a path, URL or password.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from recon_engine.models import Connection, ConnectionType


class ConnectionStoreError(Exception):
    """A connection is unknown or invalid."""


class ConnectionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: dict[str, Connection] = {}
        if path.exists():
            for raw in json.loads(path.read_text(encoding="utf-8")):
                conn = Connection(
                    id=raw["id"], type=ConnectionType(raw["type"]), config=raw["config"]
                )
                self._items[conn.id] = conn

    def add(self, connection: Connection, *, replace: bool = False) -> Connection:
        if not connection.id.strip():
            raise ConnectionStoreError("A connection needs an ID")
        if connection.id in self._items and not replace:
            raise ConnectionStoreError(f"Connection '{connection.id}' already exists")
        self._items[connection.id] = connection
        self._save()
        return connection

    def remove(self, connection_id: str) -> None:
        self.get(connection_id)
        del self._items[connection_id]
        self._save()

    def get(self, connection_id: str) -> Connection:
        try:
            return self._items[connection_id]
        except KeyError:
            raise ConnectionStoreError(
                f"No connection '{connection_id}'; available: {sorted(self._items)}"
            ) from None

    def all(self) -> list[Connection]:
        return [self._items[i] for i in sorted(self._items)]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [{"id": c.id, "type": c.type.value, "config": c.config} for c in self.all()]
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".connections-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
