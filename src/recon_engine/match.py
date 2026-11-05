"""Row-level matching of two extracts. Produces the gap that checks later explain.

Rows are grouped by key on each side and their amounts summed per key. A key on
both sides with equal sums is matched; with unequal sums it is an amount
difference; on one side only, each of its rows is unmatched. Rows with any
missing key column never match. Nothing is dropped silently: rows outside the
period are listed as excluded, and rows whose amount is empty or not a number
are listed as invalid (a data-quality problem) and left out of the totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from recon_engine.hashing import canonical_value, to_decimal
from recon_engine.models import (
    AmountDifference,
    ExtractData,
    InvalidRow,
    MatchResult,
    MatchSpec,
    Period,
    UnmatchedRow,
    row_id,
)

_Key = tuple[Any, ...]


class MatchError(ValueError):
    """The extracts cannot be matched as specified."""


@dataclass
class _Side:
    groups: dict[_Key, list[tuple[str, Decimal]]] = field(default_factory=dict)
    unkeyed: list[UnmatchedRow] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    invalid: list[InvalidRow] = field(default_factory=list)
    total: Decimal = Decimal(0)


def as_date(value: Any, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            pass
    raise MatchError(f"{where}: date value {value!r} is not an ISO date (YYYY-MM-DD)")


def _prepare(
    label: str,
    data: ExtractData,
    keys: list[str],
    amount_column: str,
    date_column: str | None,
    period: Period | None,
) -> _Side:
    frame = data.frame
    extract_id = data.extract.id
    needed = [*keys, amount_column, *([date_column] if period and date_column else [])]
    for column in needed:
        if column not in frame.columns:
            raise MatchError(
                f"side {label} (extract {extract_id}) has no column '{column}'; "
                f"available: {list(frame.columns)}"
            )

    side = _Side()
    for ordinal, row in enumerate(frame[needed].itertuples(index=False, name=None)):
        rid = row_id(extract_id, ordinal)
        where = f"side {label} row {rid}"
        if period is not None and not period.contains(as_date(row[-1], where)):
            side.excluded.append(rid)
            continue

        raw_amount = row[len(keys)]
        key = tuple(canonical_value(v) for v in row[: len(keys)])
        try:
            amount = to_decimal(raw_amount)
            reason = None if amount is not None else f"amount is empty ({amount_column})"
        except ValueError:
            amount = None
            reason = f"amount {raw_amount!r} is not a number ({amount_column})"
        if amount is None:
            side.invalid.append(InvalidRow(row_id=rid, key=list(key), reason=reason))
            continue
        side.total += amount

        if any(part is None for part in key):
            side.unkeyed.append(UnmatchedRow(row_id=rid, key=list(key), amount=amount))
        else:
            side.groups.setdefault(key, []).append((rid, amount))
    return side


def _unmatched(key: _Key, rows: list[tuple[str, Decimal]]) -> list[UnmatchedRow]:
    return [UnmatchedRow(row_id=rid, key=list(key), amount=amount) for rid, amount in rows]


def _ordinal(row: UnmatchedRow) -> int:
    return int(row.row_id.rsplit(":", 1)[1])


def match(a: ExtractData, b: ExtractData, spec: MatchSpec) -> MatchResult:
    if (a.extract.id, b.extract.id) != (spec.extract_a, spec.extract_b):
        raise MatchError(
            f"Extracts ({a.extract.id}, {b.extract.id}) do not match the MatchSpec "
            f"({spec.extract_a}, {spec.extract_b})"
        )
    side_a = _prepare("A", a, spec.key_columns, spec.amount_column, spec.date_column, spec.period)
    side_b = _prepare("B", b, spec.keys_b, spec.amount_b, spec.date_b, spec.period)

    only_a = list(side_a.unkeyed)
    only_b = list(side_b.unkeyed)
    differences: list[AmountDifference] = []
    matched = 0

    for key, rows_a in side_a.groups.items():
        rows_b = side_b.groups.get(key)
        if rows_b is None:
            only_a.extend(_unmatched(key, rows_a))
            continue
        amount_a = sum((amount for _, amount in rows_a), Decimal(0))
        amount_b = sum((amount for _, amount in rows_b), Decimal(0))
        if amount_a == amount_b:
            matched += 1
        else:
            differences.append(
                AmountDifference(
                    key=list(key),
                    row_ids_a=[rid for rid, _ in rows_a],
                    row_ids_b=[rid for rid, _ in rows_b],
                    amount_a=amount_a,
                    amount_b=amount_b,
                    difference=amount_a - amount_b,
                )
            )
    for key, rows_b in side_b.groups.items():
        if key not in side_a.groups:
            only_b.extend(_unmatched(key, rows_b))

    return MatchResult(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        matched_count=matched,
        only_a=sorted(only_a, key=_ordinal),
        only_b=sorted(only_b, key=_ordinal),
        amount_differences=differences,
        excluded_a=side_a.excluded,
        excluded_b=side_b.excluded,
        invalid_a=side_a.invalid,
        invalid_b=side_b.invalid,
        total_a=side_a.total,
        total_b=side_b.total,
        gap=side_a.total - side_b.total,
    )
