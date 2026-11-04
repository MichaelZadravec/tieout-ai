"""Tax basis: one side's amounts include GST, the other side's exclude it.

Guardrails: the rate is above 0 and at most 25%; a key is claimed only if the
inclusive amount equals the exclusive amount grossed up at that rate, rounded
to the cent (or the reverse calculation rounds back exactly).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field

from recon_engine.checks.base import (
    Check,
    CheckContext,
    CheckParams,
    Claim,
    Item,
    ProposedBy,
    Side,
    other,
    register,
)
from recon_engine.models import Assumption

_CENT = Decimal("0.01")


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class TaxBasisParams(CheckParams):
    rate: Decimal = Field(gt=0, le=Decimal("0.25"), description="Tax rate, e.g. 0.10 for GST.")
    inclusive_side: Side = Field(description="The side whose amounts include tax.")


@register
class TaxBasisCheck(Check):
    name = "tax_basis"
    version = "1.0.0"
    description = (
        "Keys whose amounts differ exactly by tax: one side inclusive, the other exclusive."
    )
    Params = TaxBasisParams

    def prove(
        self, ctx: CheckContext, items: list[Item], params: TaxBasisParams
    ) -> Iterable[Claim]:
        factor = 1 + params.rate
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
            if _cents(exclusive * factor) == inclusive or _cents(inclusive / factor) == exclusive:
                claims.append(Claim(item))
        return claims

    def assumptions(
        self, ctx: CheckContext, params: TaxBasisParams, proposed_by: ProposedBy
    ) -> list[Assumption]:
        percent = f"{(params.rate * 100).normalize():f}%"
        side, excluding = params.inclusive_side.upper(), other(params.inclusive_side).upper()
        return [
            Assumption.propose(
                "tax_basis.rate",
                params.rate,
                f"Side {side} amounts include tax at {percent}; side {excluding} amounts "
                "exclude it.",
                proposed_by,
            )
        ]
