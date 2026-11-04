"""Core data model. Later phases add Assumption, Bucket and Certificate here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConnectionType(StrEnum):
    FILE = "file"
    SQL = "sql"
    POWERBI = "powerbi"


class Connection(_Model):
    """A configured data source. `config` holds credentials and is never serialised."""

    id: str
    type: ConnectionType
    config: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)


class Extract(_Model):
    """Provenance of one fetch: what was asked, of which source, and what came back."""

    id: str
    connection_id: str
    query_text: str
    query_hash: str
    columns: list[str]
    row_count: int
    column_totals: dict[str, Decimal]
    data_hash: str
    fetched_at: datetime


@dataclass(frozen=True)
class ExtractData:
    """An Extract together with the rows it describes. Rows stay inside the engine."""

    extract: Extract
    frame: pd.DataFrame


def row_id(extract_id: str, ordinal: int) -> str:
    """Stable identifier for a row: its extract plus its 0-based position in that extract."""
    return f"{extract_id}:{ordinal}"


class Period(_Model):
    """Inclusive date range."""

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.end < self.start:
            raise ValueError("period end must not be before start")
        return self

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


class MatchSpec(_Model):
    """How to pair rows of extract A with extract B.

    Column names default to the same on both sides; the `_b` fields override them
    for side B when the two sources name things differently.
    """

    extract_a: str
    extract_b: str
    key_columns: list[str] = Field(min_length=1)
    amount_column: str
    date_column: str | None = None
    key_columns_b: list[str] | None = None
    amount_column_b: str | None = None
    date_column_b: str | None = None
    period: Period | None = None

    @model_validator(mode="after")
    def _consistent(self) -> MatchSpec:
        if self.key_columns_b is not None and len(self.key_columns_b) != len(self.key_columns):
            raise ValueError(
                "key_columns and key_columns_b must have the same number of key columns"
            )
        if self.period is not None and self.date_column is None:
            raise ValueError("period requires date_column")
        return self

    @property
    def keys_b(self) -> list[str]:
        return self.key_columns_b or self.key_columns

    @property
    def amount_b(self) -> str:
        return self.amount_column_b or self.amount_column

    @property
    def date_b(self) -> str | None:
        return self.date_column_b or self.date_column


Key = list[str | bool | None]


class UnmatchedRow(_Model):
    row_id: str
    key: Key
    amount: Decimal


class AmountDifference(_Model):
    """A key present on both sides whose amounts disagree. difference = amount_a - amount_b."""

    key: Key
    row_ids_a: list[str]
    row_ids_b: list[str]
    amount_a: Decimal
    amount_b: Decimal
    difference: Decimal


class MatchResult(_Model):
    """Outcome of matching. gap = total_a - total_b, and it always equals
    sum(only_a) - sum(only_b) + sum(amount_differences)."""

    extract_a: str
    extract_b: str
    matched_count: int
    only_a: list[UnmatchedRow]
    only_b: list[UnmatchedRow]
    amount_differences: list[AmountDifference]
    excluded_a: list[str]
    excluded_b: list[str]
    total_a: Decimal
    total_b: Decimal
    gap: Decimal
