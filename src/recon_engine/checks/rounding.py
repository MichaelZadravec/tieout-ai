"""Rounding: a key on both sides whose amounts differ by no more than a cent or so.

Guardrails: only amount differences on matched keys are claimed (never a row
missing from one side); the tolerance applies per record, defaults to $0.01 and
is at most $0.05. Any tolerance loosens exact matching, so it is always logged
as an elevated assumption.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from pydantic import Field, field_validator

from recon_engine.checks.base import (
    Check,
    CheckContext,
    CheckParams,
    Claim,
    Item,
    Proposal,
    register,
)

DEFAULT_TOLERANCE = Decimal("0.01")
MAX_TOLERANCE = Decimal("0.05")


class RoundingParams(CheckParams):
    tolerance: Decimal = Field(
        default=DEFAULT_TOLERANCE,
        description=f"Largest difference per record treated as rounding. Default "
        f"${DEFAULT_TOLERANCE}, at most ${MAX_TOLERANCE}.",
    )

    @field_validator("tolerance")
    @classmethod
    def _within_limits(cls, value: Decimal) -> Decimal:
        if not 0 < value <= MAX_TOLERANCE:
            raise ValueError(f"tolerance must be above $0 and at most ${MAX_TOLERANCE}")
        return value


@register
class RoundingCheck(Check):
    name = "rounding"
    version = "1.0.0"
    description = (
        "Keys on both sides whose amounts differ by no more than a small tolerance "
        f"(default ${DEFAULT_TOLERANCE}, at most ${MAX_TOLERANCE} per record)."
    )
    Params = RoundingParams

    def prove(
        self, ctx: CheckContext, items: list[Item], params: RoundingParams
    ) -> Iterable[Claim]:
        return [
            Claim(item)
            for item in items
            if item.kind == "difference" and abs(item.amount) <= params.tolerance
        ]

    def propose_assumptions(
        self, ctx: CheckContext, params: RoundingParams, claims: list[Claim]
    ) -> list[Proposal]:
        tolerance = f"${params.tolerance}"
        return [
            Proposal(
                "rounding.tolerance",
                params.tolerance,
                f"{ctx.name('a')} and {ctx.name('b')} differ by at most {tolerance} per record: "
                "rounding, not a real difference.",
                loosens=f"loosens exact matching: accepts differences up to {tolerance} per "
                "record as rounding",
            )
        ]
