"""Timing: the same transaction dated inside the period on one side, just outside it on the other.

Typical cause: one system dates by appointment, the other by invoice date.

Guardrails: the window is at most one period (default 7 days); the counterpart
must have the same key and the same amount, and must be dated in the period
immediately before or after. Each counterpart row can prove only one item.

The assumption reports the largest lag actually found among the claimed rows,
and is elevated only when that lag exceeds 7 days: a wide window that only
matched short lags loosens nothing in practice.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from pydantic import Field

from tieout_ai.checks.base import (
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
from tieout_ai.hashing import canonical_value, to_decimal
from tieout_ai.match import as_date
from tieout_ai.models import Period

DEFAULT_SHIFT_DAYS = 7
ELEVATED_LAG_DAYS = 7  # an actual lag beyond this is an elevated assumption


class TimingParams(CheckParams):
    max_shift_days: int = Field(
        default=DEFAULT_SHIFT_DAYS,
        ge=1,
        description="Largest number of days between the two sides' dates for one row. "
        f"At most the length of the period. Default {DEFAULT_SHIFT_DAYS}. Claims whose actual "
        f"lag exceeds {ELEVATED_LAG_DAYS} days are logged as an elevated assumption.",
    )


Counterparts = dict[tuple, list[tuple[str, Decimal, date]]]


@register
class TimingCheck(Check):
    name = "timing"
    version = "1.2.0"
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

    def propose_assumptions(
        self, ctx: CheckContext, params: TimingParams, claims: list[Claim]
    ) -> list[Proposal]:
        lag = max(_lag(ctx, claim) for claim in claims)
        a, b = ctx.name("a"), ctx.name("b")
        return [
            Proposal(
                "timing.max_shift_days",
                {"window_days": params.max_shift_days, "max_lag_days": lag},
                f"each is dated inside the period in one of {a} and {b} and outside it in the "
                f"other, at most {lag} days apart, with the same key and amount: one "
                "transaction recorded on a different date basis.",
                loosens=(
                    f"loosens the default: matches dates up to {lag} days apart "
                    f"(default limit {ELEVATED_LAG_DAYS})"
                    if lag > ELEVATED_LAG_DAYS
                    else None
                ),
            ),
            Proposal(
                "timing.date_basis",
                {a: ctx.spec.date_column, b: ctx.spec.date_b},
                f"{a} dates the transaction by '{ctx.spec.date_column}' and {b} by "
                f"'{ctx.spec.date_b}'.",
            ),
        ]


def _lag(ctx: CheckContext, claim: Claim) -> int:
    """Days between a claimed row and the counterpart that proved it."""
    side: Side = "a" if claim.item.kind == "only_a" else "b"
    [row_id], [counterpart] = claim.item.row_ids, claim.evidence
    inside_column, outside_column = ctx.date_column(side), ctx.date_column(other(side))
    assert inside_column is not None and outside_column is not None
    inside = as_date(ctx.value(row_id, inside_column), row_id)
    outside = as_date(ctx.value(counterpart, outside_column), counterpart)
    return abs((outside - inside).days)


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
