"""Status filter: one side includes rows of a status (e.g. VOIDED) that the other side excludes.

Guardrail: a row is claimed only if its own status value in the source extract
is exactly one of the named statuses. Naming several spellings of one status
(VOIDED, Voided) loosens that, so it is logged as an elevated assumption.
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
    Proposal,
    Side,
    other,
    register,
)
from recon_engine.hashing import canonical_value


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

    def propose_assumptions(self, ctx: CheckContext, params: StatusFilterParams) -> list[Proposal]:
        including, excluding = ctx.name(params.side), ctx.name(other(params.side))
        statuses = ", ".join(repr(s) for s in params.statuses)
        spellings: dict[str, list[str]] = {}
        for status in params.statuses:
            spellings.setdefault(status.strip().casefold(), []).append(status)
        variants = [
            ", ".join(repr(s) for s in group) for group in spellings.values() if len(group) > 1
        ]
        return [
            Proposal(
                "status_filter.statuses",
                {
                    "included_by": including,
                    "column": params.status_column,
                    "statuses": params.statuses,
                },
                f"{including} includes them with '{params.status_column}' of {statuses}, and "
                f"{excluding} excludes them.",
                loosens=(
                    "loosens the default: counts "
                    + "; ".join(f"{group} as one status" for group in variants)
                    if variants
                    else None
                ),
            )
        ]
