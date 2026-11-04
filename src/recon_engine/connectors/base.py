"""Connector interface and the one place extracts are built and hashed."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from recon_engine.hashing import column_totals, hash_frame, hash_query, is_missing
from recon_engine.models import Connection, ConnectionType, Extract, ExtractData


class ConnectorError(Exception):
    """A source could not be read. Messages never include credentials."""


class UnsafeQueryError(ConnectorError):
    """A query was rejected because it is not a single read-only SELECT."""


class Connector(ABC):
    connection_type: ClassVar[ConnectionType]

    def __init__(self, connection: Connection) -> None:
        if connection.type != self.connection_type:
            raise ConnectorError(
                f"{type(self).__name__} expects a '{self.connection_type}' connection, "
                f"got '{connection.type}' for connection '{connection.id}'"
            )
        self.connection = connection

    @abstractmethod
    def fetch(self, query: str | None = None) -> ExtractData:
        """Read from the source and return the rows with their hashed Extract."""


def _native(value: Any) -> Any:
    if is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.generic):
        return value.item()
    return value


def build_extract(connection_id: str, query_text: str, frame: pd.DataFrame) -> ExtractData:
    """Normalise rows to plain Python values and record their provenance and hashes."""
    columns = [str(c) for c in frame.columns]
    duplicates = sorted({c for c in columns if columns.count(c) > 1})
    if duplicates:
        raise ConnectorError(f"Source returned duplicate column names: {duplicates}")

    rows = [[_native(v) for v in row] for row in frame.itertuples(index=False, name=None)]
    clean = pd.DataFrame(rows, columns=columns, dtype=object)
    extract = Extract(
        id=f"ext_{uuid.uuid4().hex[:12]}",
        connection_id=connection_id,
        query_text=query_text,
        query_hash=hash_query(query_text),
        columns=columns,
        row_count=len(clean),
        column_totals=column_totals(clean),
        data_hash=hash_frame(clean),
        fetched_at=datetime.now(UTC),
    )
    return ExtractData(extract=extract, frame=clean)
