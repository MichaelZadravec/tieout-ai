"""Core data model."""

from __future__ import annotations

from collections.abc import Sequence
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


def source_names(connection_a: str, connection_b: str) -> tuple[str, str]:
    """What to call the two sides in anything a person reads: their connection names."""
    if connection_a == connection_b:
        return f"{connection_a} (A)", f"{connection_b} (B)"
    return connection_a, connection_b


def key_label(key: Sequence[Any]) -> str:
    """A key as a person reads it. Blanks show as (blank); edge whitespace is quoted."""

    def part(value: Any) -> str:
        if value is None:
            return "(blank)"
        text = str(value)
        return f'"{text}"' if text != text.strip() else text

    return " / ".join(part(value) for value in key)


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


class InvalidRow(_Model):
    """A row that could not be used, e.g. its amount is empty. Never silently dropped."""

    row_id: str
    key: Key
    reason: str


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
    invalid_a: list[InvalidRow] = Field(default_factory=list)
    invalid_b: list[InvalidRow] = Field(default_factory=list)
    total_a: Decimal
    total_b: Decimal
    gap: Decimal


class Assumption(_Model):
    """A parameter choice that affects the result. Only a human confirms one, via the CLI.

    `scope` lists the keys (as `key_label` shows them) of the records the
    assumption covers; it is empty for one about the reconciliation as a whole.
    The id is derived from the parameter, value and scope, so proposing the same
    thing twice gives the same assumption, and the same reasoning applied to
    different records gives a different one.
    """

    id: str
    description: str
    parameter: str
    value: Any
    scope: list[str] = Field(default_factory=list)
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
        scope: Sequence[str] = (),
    ) -> Assumption:
        basis = [parameter, value, list(scope)] if scope else [parameter, value]
        digest = sha256_hex(canonical_json(basis))
        return cls(
            id=f"asm_{digest[:12]}",
            description=description,
            parameter=parameter,
            value=value,
            scope=list(scope),
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
    RECONCILED = "RECONCILED"  # every item explained and every assumption confirmed
    PROVISIONAL = "PROVISIONAL"  # every item explained, but some assumptions unconfirmed
    OPEN = "OPEN"  # some items are unexplained


class MatchSummary(_Model):
    matched_count: int
    only_a_count: int
    only_b_count: int
    difference_count: int
    excluded_a_count: int
    excluded_b_count: int
    invalid_a_count: int = 0
    invalid_b_count: int = 0
    total_a: Decimal
    total_b: Decimal
    gap: Decimal


class ResidualItem(_Model):
    """A discrepancy no bucket explains. `amount` is its contribution to the gap."""

    id: str
    kind: Literal["only_a", "only_b", "difference", "data_quality"]
    key: Key
    amount: Decimal
    row_ids: list[str]
    reason: str | None = None


class CheckRun(_Model):
    """A check that ran and found nothing. Recorded so reviewers see what was tested."""

    check_name: str
    check_version: str
    parameters: dict[str, Any]


class Certificate(_Model):
    """The engine's record of one reconciliation.

    Status: RECONCILED when every discrepancy item is explained and every assumption
    behind the explanations is confirmed by a person; PROVISIONAL when everything is
    explained but some assumptions are unconfirmed; OPEN when anything is unexplained.

    Assumptions are a snapshot of their state when the certificate was issued.
    `supersedes` names the earlier certificate this one replaces. Checks that ran
    and found nothing are listed in `checks_without_findings`, without assumptions.
    `certificate_hash` covers every field except `id`, which is derived from it.
    """

    id: str
    engine_version: str
    status: CertificateStatus
    created_at: datetime
    extracts: list[Extract]
    match_spec: MatchSpec
    match: MatchSummary
    buckets: list[Bucket]
    checks_without_findings: list[CheckRun] = Field(default_factory=list)
    explained: Decimal
    residual: Decimal
    residual_items: list[ResidualItem]
    assumptions: list[Assumption]
    unconfirmed_assumptions: list[str]
    supersedes: str | None = None
    certificate_hash: str

    @property
    def assumptions_confirmed(self) -> bool:
        return not self.unconfirmed_assumptions
