"""The assumption store: every assumption proposed during a reconciliation, by ID.

Buckets and certificates refer to assumptions by ID; this store holds their
current state. Proposing the same assumption again returns the stored one, so
a confirmation is never lost by re-running a check.

The file on disk is the only source of truth. Every read and every write loads
it afresh, because a person confirms with `recon confirm` in another process
while the MCP server keeps running.

`confirm` is for humans only. The CLI calls it; the MCP server must never
expose it, so an AI cannot confirm its own assumptions.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from recon_engine.models import Assumption


class AssumptionError(Exception):
    """An assumption is unknown or cannot be changed as requested."""


class AssumptionStore:
    """In memory when `path` is None, otherwise the JSON file at `path`, never cached."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._memory: dict[str, Assumption] = {}

    def _load(self) -> dict[str, Assumption]:
        if self.path is None:
            return self._memory
        if not self.path.exists():
            return {}
        items = (
            Assumption.model_validate(raw)
            for raw in json.loads(self.path.read_text(encoding="utf-8"))
        )
        return {a.id: a for a in items}

    def propose(self, assumption: Assumption) -> Assumption:
        items = self._load()
        existing = items.get(assumption.id)
        if existing is not None:
            return existing
        items[assumption.id] = assumption
        self._save(items)
        return assumption

    def get(self, assumption_id: str) -> Assumption:
        return self._get(self._load(), assumption_id)

    def get_many(self, assumption_ids: Iterable[str]) -> list[Assumption]:
        items = self._load()
        return [self._get(items, i) for i in assumption_ids]

    @staticmethod
    def _get(items: dict[str, Assumption], assumption_id: str) -> Assumption:
        try:
            return items[assumption_id]
        except KeyError:
            raise AssumptionError(f"No assumption with id '{assumption_id}'") from None

    def all(self) -> list[Assumption]:
        items = self._load()
        return [items[i] for i in sorted(items)]

    def confirm(
        self, assumption_id: str, confirmed_by: str, at: datetime | None = None
    ) -> Assumption:
        """Record a human's confirmation. Human-only: never expose this to an AI."""
        if not confirmed_by.strip():
            raise AssumptionError("A confirmation needs the name of the person confirming")
        items = self._load()
        current = self._get(items, assumption_id)
        if current.confirmed:
            return current
        confirmed = current.model_copy(
            update={
                "confirmed": True,
                "confirmed_by": confirmed_by.strip(),
                "confirmed_at": at or datetime.now(UTC),
            }
        )
        items[assumption_id] = confirmed
        self._save(items)
        return confirmed

    def __contains__(self, assumption_id: object) -> bool:
        return assumption_id in self._load()

    def __len__(self) -> int:
        return len(self._load())

    def _save(self, items: dict[str, Assumption]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [items[i].model_dump(mode="json") for i in sorted(items)], indent=2, sort_keys=True
        )
        # Write then rename, so a crash never leaves a half-written store.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".assumptions-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload + "\n")
        os.replace(tmp, self.path)
