"""Core data model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from recon_engine.hashing import canonical_json, sha256_hex


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


class Assumption(_Model):
    """A parameter choice that affects the result. Only a human confirms one, via the CLI.

    The id is derived from the parameter and value, so proposing the same thing
    twice gives the same assumption.
    """

    id: str
    description: str
    parameter: str
    value: Any
    proposed_by: Literal["ai", "human"]
    confirmed: bool = False
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None

    @classmethod
    def propose(
        cls,
        parameter: str,
        value: Any,
        description: str,
        proposed_by: Literal["ai", "human"] = "ai",
    ) -> Assumption:
        digest = sha256_hex(canonical_json([parameter, value]))
        return cls(
            id=f"asm_{digest[:12]}",
            description=description,
            parameter=parameter,
            value=value,
            proposed_by=proposed_by,
        )


class Bucket(_Model):
    """What one check proved: the discrepancy items it claims and their summed amount.

    Assumptions are referenced by ID; their state lives in the AssumptionStore.

    `amount` is the bucket's contribution to the gap (A minus B). `row_ids` are the
    rows behind the claimed items; `evidence_row_ids` are rows the check read as
    proof without claiming them (for example a timing counterpart outside the period).
    """

    check_name: str
    check_version: str
    parameters: dict[str, Any]
    item_ids: list[str]
    row_ids: list[str]
    evidence_row_ids: list[str]
    amount: Decimal
    row_count: int
    assumption_ids: list[str]

    @model_validator(mode="after")
    def _counted(self) -> Bucket:
        if self.row_count != len(self.row_ids):
            raise ValueError("row_count must equal the number of row_ids")
        return self


class CertificateStatus(StrEnum):
    RECONCILED = "RECONCILED"
    OPEN = "OPEN"


class MatchSummary(_Model):
    matched_count: int
    only_a_count: int
    only_b_count: int
    difference_count: int
    excluded_a_count: int
    excluded_b_count: int
    total_a: Decimal
    total_b: Decimal
    gap: Decimal


class ResidualItem(_Model):
    """A discrepancy no bucket explains. `amount` is its contribution to the gap."""

    id: str
    kind: Literal["only_a", "only_b", "difference"]
    key: Key
    amount: Decimal
    row_ids: list[str]


class Certificate(_Model):
    """The engine's signed-off record of one reconciliation.

    RECONCILED means every discrepancy item is explained, so the residual is zero.
    Anything else is OPEN. Assumptions are a snapshot of their state when the
    certificate was issued; `unconfirmed_assumptions` flags the ones no human has
    confirmed. `certificate_hash` covers every other field except `id`, which is
    derived from the hash.
    """

    id: str
    engine_version: str
    status: CertificateStatus
    created_at: datetime
    extracts: list[Extract]
    match_spec: MatchSpec
    match: MatchSummary
    buckets: list[Bucket]
    explained: Decimal
    residual: Decimal
    residual_items: list[ResidualItem]
    assumptions: list[Assumption]
    unconfirmed_assumptions: list[str]
    certificate_hash: str

    @property
    def assumptions_confirmed(self) -> bool:
        return not self.unconfirmed_assumptions
