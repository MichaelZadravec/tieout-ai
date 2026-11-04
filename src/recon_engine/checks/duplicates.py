"""Duplicates: one side repeats a row that the other side has once.

Guardrails: every repeated row must be identical in every column of its
extract, and the other side's amount for that key must equal one copy.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from recon_engine.checks.base import (
    Check,
    CheckContext,
    CheckParams,
    Claim,
    Item,
    ProposedBy,
    Side,
    register,
)
from recon_engine.hashing import canonical_json, to_decimal
from recon_engine.models import Assumption


class DuplicatesParams(CheckParams):
    side: Side = Field(description="The side that contains the repeated rows.")


@register
class DuplicatesCheck(Check):
    name = "duplicates"
    version = "1.0.0"
    description = (
        "Keys where one side has the same row several times, identical in every column, "
        "and the other side has it once."
    )
    Params = DuplicatesParams

    def prove(
        self, ctx: CheckContext, items: list[Item], params: DuplicatesParams
    ) -> Iterable[Claim]:
        amount_column = ctx.amount_column(params.side)
        claims = []
        for item in items:
            if item.kind != "difference":
                continue
            repeated = item.row_ids_a if params.side == "a" else item.row_ids_b
            once = item.amount_b if params.side == "a" else item.amount_a
            if len(repeated) < 2:
                continue
            copies = {canonical_json(list(ctx.row(row_id).values())) for row_id in repeated}
            if len(copies) != 1:
                continue
            if to_decimal(ctx.value(repeated[0], amount_column)) == once:
                claims.append(Claim(item))
        return claims

    def assumptions(
        self, ctx: CheckContext, params: DuplicatesParams, proposed_by: ProposedBy
    ) -> list[Assumption]:
        return [
            Assumption.propose(
                "duplicates.side",
                params.side,
                f"Rows on side {params.side.upper()} that are identical in every column are "
                "duplicate copies of one transaction.",
                proposed_by,
            )
        ]
