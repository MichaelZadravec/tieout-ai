"""The assumption store: every assumption proposed during a reconciliation, by ID.

Buckets and certificates refer to assumptions by ID; this store holds their
current state. Proposing the same assumption again returns the stored one, so
a confirmation is never lost by re-running a check.

`confirm` is for humans only. The CLI calls it; the MCP server (Phase 4) must
never expose it, so an AI cannot confirm its own assumptions.
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
    """In memory when `path` is None, otherwise persisted as JSON at `path`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._items: dict[str, Assumption] = {}
        if path is not None and path.exists():
            for raw in json.loads(path.read_text(encoding="utf-8")):
                assumption = Assumption.model_validate(raw)
                self._items[assumption.id] = assumption

    def propose(self, assumption: Assumption) -> Assumption:
        existing = self._items.get(assumption.id)
        if existing is not None:
            return existing
        self._items[assumption.id] = assumption
        self._save()
        return assumption

    def get(self, assumption_id: str) -> Assumption:
        try:
            return self._items[assumption_id]
        except KeyError:
            raise AssumptionError(f"No assumption with id '{assumption_id}'") from None

    def get_many(self, assumption_ids: Iterable[str]) -> list[Assumption]:
        return [self.get(i) for i in assumption_ids]

    def all(self) -> list[Assumption]:
        return [self._items[i] for i in sorted(self._items)]

    def confirm(
        self, assumption_id: str, confirmed_by: str, at: datetime | None = None
    ) -> Assumption:
        """Record a human's confirmation. Human-only: never expose this to an AI."""
        if not confirmed_by.strip():
            raise AssumptionError("A confirmation needs the name of the person confirming")
        current = self.get(assumption_id)
        if current.confirmed:
            return current
        confirmed = current.model_copy(
            update={
                "confirmed": True,
                "confirmed_by": confirmed_by.strip(),
                "confirmed_at": at or datetime.now(UTC),
            }
        )
        self._items[assumption_id] = confirmed
        self._save()
        return confirmed

    def __contains__(self, assumption_id: object) -> bool:
        return assumption_id in self._items

    def __len__(self) -> int:
        return len(self._items)

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [a.model_dump(mode="json") for a in self.all()], indent=2, sort_keys=True
        )
        # Write then rename, so a crash never leaves a half-written store.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".assumptions-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload + "\n")
        os.replace(tmp, self.path)
