"""Timing: the same transaction dated inside the period on one side, just outside it on the other.

Typical cause: one system dates by appointment, the other by invoice date.

Guardrails: the window is at most one period; the counterpart must have the same
key and the same amount, and must be dated in the period immediately before or
after. Each counterpart row can prove only one item.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from pydantic import Field

from recon_engine.checks.base import (
    Check,
    CheckContext,
    CheckParameterError,
    CheckParams,
    Claim,
    Item,
    ProposedBy,
    Side,
    other,
    register,
)
from recon_engine.hashing import canonical_value, to_decimal
from recon_engine.match import as_date
from recon_engine.models import Assumption, Period


class TimingParams(CheckParams):
    max_shift_days: int = Field(
        ge=1,
        description="Largest number of days between the two sides' dates for one row. "
        "At most the length of the period.",
    )


Counterparts = dict[tuple, list[tuple[str, Decimal, date]]]


@register
class TimingCheck(Check):
    name = "timing"
    version = "1.0.0"
    description = (
        "Rows inside the period on one side whose counterpart (same key, same amount) is "
        "dated in the adjacent period on the other side."
    )
    Params = TimingParams

    def prove(self, ctx: CheckContext, items: list[Item], params: TimingParams) -> Iterable[Claim]:
        period = ctx.spec.period
        if period is None or ctx.spec.date_column is None:
            raise CheckParameterError("timing needs a MatchSpec period and date columns")
        length = (period.end - period.start).days + 1
        if params.max_shift_days > length:
            raise CheckParameterError(
                f"max_shift_days must be at most one period ({length} days); "
                f"got {params.max_shift_days}"
            )

        claims = []
        for side in ("a", "b"):
            counterparts = _adjacent_rows(ctx, other(side), period, length)
            date_column = ctx.date_column(side)
            assert date_column is not None
            used: set[str] = set()
            for item in items:
                if item.kind != f"only_{side}" or item.key not in counterparts:
                    continue
                [row_id] = item.row_ids
                inside = as_date(ctx.value(row_id, date_column), row_id)
                amount = item.amount_a if side == "a" else item.amount_b
                for candidate, candidate_amount, outside in counterparts[item.key]:
                    if candidate in used or candidate_amount != amount:
                        continue
                    if abs((outside - inside).days) <= params.max_shift_days:
                        used.add(candidate)
                        claims.append(Claim(item, (candidate,)))
                        break
        return claims

    def assumptions(
        self, ctx: CheckContext, params: TimingParams, proposed_by: ProposedBy
    ) -> list[Assumption]:
        days = params.max_shift_days
        return [
            Assumption.propose(
                "timing.max_shift_days",
                days,
                f"A row dated inside the period on one side and up to {days} days outside it "
                "on the other, with the same key and amount, is one transaction recorded on a "
                "different date basis.",
                proposed_by,
            ),
            Assumption.propose(
                "timing.date_basis",
                [ctx.spec.date_column, ctx.spec.date_b],
                f"Side A is dated by '{ctx.spec.date_column}' and side B by '{ctx.spec.date_b}'.",
                proposed_by,
            ),
        ]


def _adjacent_rows(ctx: CheckContext, side: Side, period: Period, length: int) -> Counterparts:
    """Rows on `side` excluded from the period but dated in the period just before or after."""
    date_column = ctx.date_column(side)
    assert date_column is not None
    earliest = period.start - timedelta(days=length)
    latest = period.end + timedelta(days=length)
    rows: Counterparts = {}
    for row_id in ctx.excluded(side):
        day = as_date(ctx.value(row_id, date_column), row_id)
        if not earliest <= day <= latest:
            continue
        key = tuple(canonical_value(ctx.value(row_id, c)) for c in ctx.key_columns(side))
        if any(part is None for part in key):
            continue
        try:
            amount = to_decimal(ctx.value(row_id, ctx.amount_column(side)))
        except ValueError:
            continue
        if amount is not None:
            rows.setdefault(key, []).append((row_id, amount, day))
    return rows
