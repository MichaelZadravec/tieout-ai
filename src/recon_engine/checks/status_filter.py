"""Status filter: one side includes rows of a status (e.g. VOIDED) that the other side excludes.

Guardrail: a row is claimed only if its own status value in the source extract
is exactly one of the named statuses.
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
    other,
    register,
)
from recon_engine.hashing import canonical_value
from recon_engine.models import Assumption


class StatusFilterParams(CheckParams):
    side: Side = Field(description="The side that includes the rows the other side excludes.")
    status_column: str = Field(min_length=1, description="Status column on that side.")
    statuses: list[str] = Field(
        min_length=1, max_length=20, description="Exact status values the other side excludes."
    )


@register
class StatusFilterCheck(Check):
    name = "status_filter"
    version = "1.0.0"
    description = (
        "Rows present on one side only whose status on that side is one the other side filters "
        "out (for example voided or draft invoices)."
    )
    Params = StatusFilterParams

    def prove(
        self, ctx: CheckContext, items: list[Item], params: StatusFilterParams
    ) -> Iterable[Claim]:
        ctx.require_column(params.side, params.status_column)
        wanted = set(params.statuses)
        claims = []
        for item in items:
            if item.kind != f"only_{params.side}":
                continue
            [row_id] = item.row_ids
            if canonical_value(ctx.value(row_id, params.status_column)) in wanted:
                claims.append(Claim(item))
        return claims

    def assumptions(
        self, ctx: CheckContext, params: StatusFilterParams, proposed_by: ProposedBy
    ) -> list[Assumption]:
        side, excluding = params.side.upper(), other(params.side).upper()
        return [
            Assumption.propose(
                "status_filter.statuses",
                params.statuses,
                f"Side {side} includes rows whose '{params.status_column}' is one of "
                f"{params.statuses}; side {excluding} excludes them.",
                proposed_by,
            )
        ]
