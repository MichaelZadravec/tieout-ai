"""Tax basis: one side's amounts include GST, the other side's exclude it.

Guardrails: the rate is above 0 and at most 25%; a key is claimed only if the
inclusive amount equals the exclusive amount grossed up at that rate, rounded
to the cent (or the reverse calculation rounds back exactly).

`rounding_tolerance` (default $0, at most $0.01) also accepts amounts up to that
far from the correctly rounded value, for systems that round tax per line. Any
tolerance is logged as an elevated assumption.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field, field_validator

from tieout_ai.checks.base import (
    Check,
    CheckContext,
    CheckParams,
    Claim,
    Item,
    Proposal,
    Side,
    other,
    register,
)

_CENT = Decimal("0.01")
MAX_ROUNDING_TOLERANCE = Decimal("0.01")


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class TaxBasisParams(CheckParams):
    rate: Decimal = Field(gt=0, le=Decimal("0.25"), description="Tax rate, e.g. 0.10 for GST.")
    inclusive_side: Side = Field(description="The side whose amounts include tax.")
    rounding_tolerance: Decimal = Field(
        default=Decimal(0),
        description="How far an amount may be from the correctly rounded value. "
        f"Default $0, at most ${MAX_ROUNDING_TOLERANCE}.",
    )

    @field_validator("rounding_tolerance")
    @classmethod
    def _within_limits(cls, value: Decimal) -> Decimal:
        if not 0 <= value <= MAX_ROUNDING_TOLERANCE:
            raise ValueError(f"rounding_tolerance must be between $0 and ${MAX_ROUNDING_TOLERANCE}")
        return value


@register
class TaxBasisCheck(Check):
    name = "tax_basis"
    version = "1.1.0"
    description = (
        "Keys whose amounts differ exactly by tax: one side inclusive, the other exclusive."
    )
    Params = TaxBasisParams

    def prove(
        self, ctx: CheckContext, items: list[Item], params: TaxBasisParams
    ) -> Iterable[Claim]:
        factor = 1 + params.rate
        tolerance = params.rounding_tolerance
        claims = []
        for item in items:
            if item.kind != "difference":
                continue
            assert item.amount_a is not None and item.amount_b is not None
            inclusive, exclusive = (
                (item.amount_a, item.amount_b)
                if params.inclusive_side == "a"
                else (item.amount_b, item.amount_a)
            )
            if exclusive == 0 or (inclusive > 0) != (exclusive > 0):
                continue
            if (
                abs(_cents(exclusive * factor) - inclusive) <= tolerance
                or abs(_cents(inclusive / factor) - exclusive) <= tolerance
            ):
                claims.append(Claim(item))
        return claims

    def propose_assumptions(
        self, ctx: CheckContext, params: TaxBasisParams, claims: list[Claim]
    ) -> list[Proposal]:
        percent = f"{(params.rate * 100).normalize():f}%"
        tolerance = params.rounding_tolerance
        including = ctx.name(params.inclusive_side)
        excluding = ctx.name(other(params.inclusive_side))
        return [
            Proposal(
                "tax_basis.rate",
                {"rate": params.rate, "inclusive": including}
                | ({"rounding_tolerance": tolerance} if tolerance else {}),
                f"{including} amounts include {percent} tax and {excluding} amounts exclude it"
                + (f", to within ${tolerance} of the rounded value." if tolerance else "."),
                loosens=(
                    f"loosens the default: accepts tax amounts up to ${tolerance} off the "
                    "rounded value"
                    if tolerance
                    else None
                ),
            )
        ]
