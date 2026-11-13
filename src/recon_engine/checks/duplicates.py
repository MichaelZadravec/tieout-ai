"""Duplicates: one side repeats a row that the other side has once.

Guardrails: every repeated row must be identical in the compared columns (by
default every column of its extract), and the other side's amount for that key
must equal one copy. The compared columns must always include the match's core
columns on that side: its key, its amount and its date. Otherwise one invoice
recorded on two different dates could be claimed as a copy.

`compare_columns` lets a reviewer treat rows that differ only in load metadata
(a load ID, an export timestamp) as duplicates. The columns compared, and so the
ones ignored, are logged as an elevated assumption.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from recon_engine.checks.base import (
    Check,
    CheckContext,
    CheckParameterError,
    CheckParams,
    Claim,
    Item,
    Proposal,
    Side,
    other,
    register,
)
from recon_engine.hashing import canonical_json, to_decimal


class DuplicatesParams(CheckParams):
    side: Side = Field(description="The side that contains the repeated rows.")
    compare_columns: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Columns that must be identical for rows to count as copies. "
        "Default: every column. Must include the match key, amount and date columns.",
    )


def _compared(ctx: CheckContext, params: DuplicatesParams) -> list[str]:
    return params.compare_columns or list(ctx.data(params.side).extract.columns)


def _core_columns(ctx: CheckContext, side: Side) -> list[str]:
    date = ctx.date_column(side)
    return [*ctx.key_columns(side), ctx.amount_column(side), *([date] if date else [])]


@register
class DuplicatesCheck(Check):
    name = "duplicates"
    version = "1.2.0"
    description = (
        "Keys where one side has the same row several times (identical in every column, or "
        "in the columns named by compare_columns) and the other side has it once."
    )
    Params = DuplicatesParams

    def prove(
        self, ctx: CheckContext, items: list[Item], params: DuplicatesParams
    ) -> Iterable[Claim]:
        amount_column = ctx.amount_column(params.side)
        columns = _compared(ctx, params)
        for column in columns:
            ctx.require_column(params.side, column)
        missing = [c for c in _core_columns(ctx, params.side) if c not in columns]
        if missing:
            raise CheckParameterError(
                "compare_columns must keep the match's core columns (key, amount and dates); "
                f"missing: {', '.join(repr(c) for c in missing)}"
            )

        claims = []
        for item in items:
            if item.kind != "difference":
                continue
            repeated = item.row_ids_a if params.side == "a" else item.row_ids_b
            once = item.amount_b if params.side == "a" else item.amount_a
            if len(repeated) < 2:
                continue
            copies = {
                canonical_json([ctx.value(row_id, c) for c in columns]) for row_id in repeated
            }
            if len(copies) != 1:
                continue
            if to_decimal(ctx.value(repeated[0], amount_column)) == once:
                claims.append(Claim(item))
        return claims

    def propose_assumptions(self, ctx: CheckContext, params: DuplicatesParams) -> list[Proposal]:
        repeating, once = ctx.name(params.side), ctx.name(other(params.side))
        compared = _compared(ctx, params)
        ignored = [c for c in ctx.data(params.side).extract.columns if c not in compared]
        basis = (
            f"identical in {compared}; differences in {ignored} are ignored"
            if ignored
            else "identical in every column"
        )
        return [
            Proposal(
                "duplicates.side",
                repeating,
                f"the repeated rows in {repeating} are copies of one transaction that {once} "
                "records once.",
            ),
            Proposal(
                "duplicates.compare_columns",
                compared,
                f"rows in {repeating} count as copies when they are {basis}.",
                loosens=(
                    f"loosens the default: ignores columns {', '.join(ignored)}"
                    if ignored
                    else None
                ),
            ),
        ]
