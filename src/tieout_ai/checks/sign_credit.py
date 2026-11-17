"""Sign and credit notes.

Two modes:

- sign_flip: a key whose amounts are equal and opposite on the two sides (a credit
  note shown positive on one side and negative on the other).
- missing_credit_notes: rows on one side only that are credit notes the other side
  leaves out. Guardrails: the row's amount must be negative and its type value in the
  source must be one of the named credit types.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import Field, model_validator

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
from tieout_ai.hashing import canonical_value


class SignCreditParams(CheckParams):
    mode: Literal["sign_flip", "missing_credit_notes"]
    side: Side | None = Field(
        default=None, description="missing_credit_notes: the side that has the credit notes."
    )
    type_column: str | None = Field(
        default=None, min_length=1, description="missing_credit_notes: document type column."
    )
    credit_types: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="missing_credit_notes: type values that mark a credit note.",
    )

    @model_validator(mode="after")
    def _mode_fields(self) -> SignCreditParams:
        extras = (self.side, self.type_column, self.credit_types)
        if self.mode == "missing_credit_notes" and None in extras:
            raise ValueError("missing_credit_notes needs side, type_column and credit_types")
        if self.mode == "sign_flip" and any(v is not None for v in extras):
            raise ValueError("sign_flip takes no other parameters")
        return self


@register
class SignCreditCheck(Check):
    name = "sign_credit"
    version = "1.0.0"
    description = (
        "Credit notes: amounts equal and opposite on the two sides (sign_flip), or negative "
        "credit-note rows that one side leaves out (missing_credit_notes)."
    )
    Params = SignCreditParams

    def prove(
        self, ctx: CheckContext, items: list[Item], params: SignCreditParams
    ) -> Iterable[Claim]:
        if params.mode == "sign_flip":
            return [
                Claim(item)
                for item in items
                if item.kind == "difference"
                and item.amount_a is not None
                and item.amount_a != 0
                and item.amount_a == -(item.amount_b or 0)
            ]

        assert params.side and params.type_column and params.credit_types
        ctx.require_column(params.side, params.type_column)
        wanted = set(params.credit_types)
        claims = []
        for item in items:
            if item.kind != f"only_{params.side}":
                continue
            [row_id] = item.row_ids
            amount = item.amount_a if params.side == "a" else item.amount_b
            is_credit = canonical_value(ctx.value(row_id, params.type_column)) in wanted
            if amount is not None and amount < 0 and is_credit:
                claims.append(Claim(item))
        return claims

    def propose_assumptions(
        self, ctx: CheckContext, params: SignCreditParams, claims: list[Claim]
    ) -> list[Proposal]:
        if params.mode == "sign_flip":
            return [
                Proposal(
                    "sign_credit.sign_flip",
                    True,
                    f"the amounts in {ctx.name('a')} and {ctx.name('b')} are equal and "
                    "opposite: the same document recorded with opposite sign conventions.",
                )
            ]
        assert params.side is not None and params.credit_types is not None
        having, missing = ctx.name(params.side), ctx.name(other(params.side))
        types = ", ".join(repr(t) for t in params.credit_types)
        return [
            Proposal(
                "sign_credit.credit_types",
                {"in": having, "column": params.type_column, "types": params.credit_types},
                f"the {having} rows, with a negative amount and '{params.type_column}' of "
                f"{types}, are credit notes that {missing} does not include.",
            )
        ]
