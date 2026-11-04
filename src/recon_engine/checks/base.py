"""Check interface, discrepancy items and the registry.

A match leaves a set of discrepancy *items* whose amounts sum exactly to the gap:

- each row only on side A           (contributes +amount)
- each row only on side B           (contributes -amount)
- each key whose amounts disagree   (contributes amount_a - amount_b)

A check tests one hypothesis against the open items and claims only the items
it can prove. The engine, not the check, sums the claimed amounts, refuses any
claim on an item that is already claimed, and keeps the claims disjoint across
a run, so buckets plus residual always equal the gap.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from recon_engine.assumptions import AssumptionStore
from recon_engine.hashing import canonical_json
from recon_engine.models import Assumption, Bucket, ExtractData, MatchResult, MatchSpec

Side = Literal["a", "b"]
ProposedBy = Literal["ai", "human"]


class CheckError(Exception):
    """A check could not run, or misbehaved."""


class CheckParameterError(CheckError):
    """Parameters were rejected by a check's guardrails."""


class CheckParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True)
class Item:
    """One unit of unexplained difference. `amount` is its contribution to the gap."""

    id: str
    kind: Literal["only_a", "only_b", "difference"]
    key: tuple[Any, ...]
    amount: Decimal
    amount_a: Decimal | None
    amount_b: Decimal | None
    row_ids_a: tuple[str, ...] = ()
    row_ids_b: tuple[str, ...] = ()

    @property
    def row_ids(self) -> tuple[str, ...]:
        return self.row_ids_a + self.row_ids_b


@dataclass(frozen=True)
class Claim:
    """An item a check can prove, plus any rows it read as evidence."""

    item: Item
    evidence: tuple[str, ...] = ()


def discrepancy_items(result: MatchResult) -> list[Item]:
    items = [
        Item(
            id=row.row_id,
            kind="only_a",
            key=tuple(row.key),
            amount=row.amount,
            amount_a=row.amount,
            amount_b=None,
            row_ids_a=(row.row_id,),
        )
        for row in result.only_a
    ]
    items += [
        Item(
            id=row.row_id,
            kind="only_b",
            key=tuple(row.key),
            amount=-row.amount,
            amount_a=None,
            amount_b=row.amount,
            row_ids_b=(row.row_id,),
        )
        for row in result.only_b
    ]
    items += [
        Item(
            id="diff:" + canonical_json(diff.key).decode("utf-8"),
            kind="difference",
            key=tuple(diff.key),
            amount=diff.difference,
            amount_a=diff.amount_a,
            amount_b=diff.amount_b,
            row_ids_a=tuple(diff.row_ids_a),
            row_ids_b=tuple(diff.row_ids_b),
        )
        for diff in result.amount_differences
    ]
    return items


@dataclass(frozen=True)
class CheckContext:
    """What a check may read: both extracts, the spec, the match and what is already claimed.

    Assumptions a check proposes go into `assumptions`, shared by every context
    derived from this one.
    """

    a: ExtractData
    b: ExtractData
    spec: MatchSpec
    result: MatchResult
    claimed: frozenset[str] = frozenset()
    assumptions: AssumptionStore = field(default_factory=AssumptionStore, compare=False)
    items: list[Item] = field(init=False, repr=False)
    _index: dict[str, Item] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (self.a.extract.id, self.b.extract.id) != (self.result.extract_a, self.result.extract_b):
            raise CheckError("CheckContext extracts do not match the MatchResult")
        items = discrepancy_items(self.result)
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "_index", {item.id: item for item in items})

    def with_claimed(self, item_ids: Iterable[str]) -> CheckContext:
        return replace(self, claimed=self.claimed | frozenset(item_ids))

    def open_items(self) -> list[Item]:
        return [item for item in self.items if item.id not in self.claimed]

    def item(self, item_id: str) -> Item:
        return self._index[item_id]

    def data(self, side: Side) -> ExtractData:
        return self.a if side == "a" else self.b

    def key_columns(self, side: Side) -> list[str]:
        return self.spec.key_columns if side == "a" else self.spec.keys_b

    def amount_column(self, side: Side) -> str:
        return self.spec.amount_column if side == "a" else self.spec.amount_b

    def date_column(self, side: Side) -> str | None:
        return self.spec.date_column if side == "a" else self.spec.date_b

    def excluded(self, side: Side) -> list[str]:
        return self.result.excluded_a if side == "a" else self.result.excluded_b

    def require_column(self, side: Side, column: str) -> None:
        columns = self.data(side).extract.columns
        if column not in columns:
            raise CheckParameterError(
                f"side {side.upper()} has no column '{column}'; available: {columns}"
            )

    def _locate(self, row_id: str) -> tuple[ExtractData, int]:
        extract_id, ordinal = row_id.rsplit(":", 1)
        for data in (self.a, self.b):
            if data.extract.id == extract_id:
                return data, int(ordinal)
        raise CheckError(f"Row {row_id} is not in either extract")

    def value(self, row_id: str, column: str) -> Any:
        data, ordinal = self._locate(row_id)
        return data.frame.iat[ordinal, data.frame.columns.get_loc(column)]

    def row(self, row_id: str) -> dict[str, Any]:
        data, ordinal = self._locate(row_id)
        return dict(zip(data.frame.columns, data.frame.iloc[ordinal].tolist(), strict=True))


def other(side: Side) -> Side:
    return "b" if side == "a" else "a"


class Check(ABC):
    name: ClassVar[str]
    version: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[BaseModel]]

    def run(
        self,
        ctx: CheckContext,
        params: dict[str, Any] | BaseModel,
        proposed_by: ProposedBy = "ai",
    ) -> Bucket:
        try:
            validated = self.Params.model_validate(
                params.model_dump() if isinstance(params, BaseModel) else params
            )
        except ValidationError as error:
            raise CheckParameterError(f"{self.name}: invalid parameters: {error}") from None

        open_items = ctx.open_items()
        open_ids = {item.id for item in open_items}
        order = {item.id: n for n, item in enumerate(open_items)}

        claims: list[Claim] = []
        seen: set[str] = set()
        for proof in self.prove(ctx, open_items, validated):
            claim = proof if isinstance(proof, Claim) else Claim(proof)
            if claim.item.id not in open_ids or claim.item.id in seen:
                raise CheckError(
                    f"Check '{self.name}' claimed item {claim.item.id} "
                    "that is already claimed or not open"
                )
            seen.add(claim.item.id)
            claims.append(claim)
        claims.sort(key=lambda c: order[c.item.id])

        row_ids = [row for claim in claims for row in claim.item.row_ids]
        evidence = list(dict.fromkeys(row for claim in claims for row in claim.evidence))
        return Bucket(
            check_name=self.name,
            check_version=self.version,
            parameters=validated.model_dump(mode="json"),
            item_ids=[claim.item.id for claim in claims],
            row_ids=row_ids,
            evidence_row_ids=evidence,
            amount=sum((claim.item.amount for claim in claims), Decimal(0)),
            row_count=len(row_ids),
            assumption_ids=[
                ctx.assumptions.propose(a).id
                for a in self.propose_assumptions(ctx, validated, proposed_by)
            ],
        )

    @abstractmethod
    def prove(self, ctx: CheckContext, items: list[Item], params: Any) -> Iterable[Claim | Item]:
        """Return the open items this hypothesis proves. Return nothing if it is wrong."""

    def propose_assumptions(
        self, ctx: CheckContext, params: Any, proposed_by: ProposedBy
    ) -> list[Assumption]:
        return []


_REGISTRY: dict[str, Check] = {}


def register(cls: type[Check]) -> type[Check]:
    if cls.name in _REGISTRY:
        raise CheckError(f"A check named '{cls.name}' is already registered")
    _REGISTRY[cls.name] = cls()
    return cls


def get_check(name: str) -> Check:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise CheckError(f"No check named '{name}'; available: {sorted(_REGISTRY)}") from None


def list_checks() -> list[Check]:
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def run_checks(
    ctx: CheckContext,
    plan: Sequence[tuple[str, dict[str, Any]]],
    proposed_by: ProposedBy = "ai",
) -> list[Bucket]:
    """Run checks in order; each sees only the items earlier checks left open."""
    buckets = []
    for name, params in plan:
        bucket = get_check(name).run(ctx, params, proposed_by)
        ctx = ctx.with_claimed(bucket.item_ids)
        buckets.append(bucket)
    return buckets
