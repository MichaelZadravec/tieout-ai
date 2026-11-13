"""Conservation checks and certificate issue.

`certify` trusts nothing a bucket says about itself. It recomputes every bucket
from the match's discrepancy items and refuses to issue a certificate unless:

- every item a bucket claims exists in this reconciliation
- no item, and no row, is in more than one bucket
- each bucket's amount is the sum of its items, and its rows are their rows
- every referenced assumption is in the assumption store
- the items sum to the gap, and buckets + residual equal the gap exactly
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from recon_engine import __version__
from recon_engine.checks import CheckContext
from recon_engine.hashing import canonical_json, sha256_hex
from recon_engine.models import (
    Bucket,
    Certificate,
    CertificateStatus,
    CheckRun,
    MatchSummary,
    ResidualItem,
)
from recon_engine.signing import TrustedKey

_UNHASHED = {"id", "certificate_hash"}


class ConservationError(Exception):
    """The buckets do not add up, overlap, or refer to things that do not exist."""


def certificate_hash(cert: Certificate) -> str:
    return sha256_hex(canonical_json(cert.model_dump(mode="json", exclude=_UNHASHED)))


def verify_certificate(cert: Certificate) -> bool:
    """True if the certificate's content still matches its hash and id."""
    digest = certificate_hash(cert)
    return digest == cert.certificate_hash and cert.id == f"cert_{digest[:16]}"


def verify_certificate_document(document: dict[str, Any]) -> bool:
    """True if a stored certificate, exactly as written, matches its hash and id.

    Stored certificates are checked as documents, not re-serialised through the
    current model: fields added to the model later must not break the hash of a
    certificate issued before they existed.
    """
    content = {k: v for k, v in document.items() if k not in _UNHASHED}
    digest = sha256_hex(canonical_json(content))
    return (
        digest == document.get("certificate_hash") and document.get("id") == f"cert_{digest[:16]}"
    )


def _check_conservation(ctx: CheckContext, buckets: Sequence[Bucket]) -> set[str]:
    owner: dict[str, str] = {}
    row_owner: dict[str, str] = {}
    for bucket in buckets:
        label = f"bucket '{bucket.check_name}'"
        items = []
        for item_id in bucket.item_ids:
            try:
                items.append(ctx.item(item_id))
            except KeyError:
                raise ConservationError(f"{label} claims unknown item {item_id}") from None
            if item_id in owner:
                raise ConservationError(
                    f"item {item_id} is in more than one bucket "
                    f"('{owner[item_id]}' and '{bucket.check_name}')"
                )
            owner[item_id] = bucket.check_name

        expected = sum((item.amount for item in items), Decimal(0))
        if bucket.amount != expected:
            raise ConservationError(
                f"{label} amount {bucket.amount} does not equal the sum of its items {expected}"
            )
        item_rows = [row for item in items for row in item.row_ids]
        if sorted(bucket.row_ids) != sorted(item_rows):
            raise ConservationError(f"{label} rows do not match its items")
        for row in bucket.row_ids:
            if row in row_owner:
                raise ConservationError(
                    f"row {row} is in more than one bucket "
                    f"('{row_owner[row]}' and '{bucket.check_name}')"
                )
            row_owner[row] = bucket.check_name

        for assumption_id in bucket.assumption_ids:
            if assumption_id not in ctx.assumptions:
                raise ConservationError(f"{label} refers to unknown assumption {assumption_id}")

    total = sum((item.amount for item in ctx.items), Decimal(0))
    if total != ctx.result.gap:
        raise ConservationError(f"discrepancy items sum to {total}, not the gap {ctx.result.gap}")
    return set(owner)


def certify(
    ctx: CheckContext,
    buckets: Sequence[Bucket],
    *,
    now: datetime | None = None,
    supersedes: str | None = None,
    extra_assumption_ids: Sequence[str] = (),
    confirmer_key: TrustedKey | None = None,
) -> Certificate:
    """Check conservation and issue a certificate.

    Empty buckets become `checks_without_findings`; their assumptions are not
    carried, since nothing on the certificate depends on them. `extra_assumption_ids`
    are assumptions proposed for the reconciliation as a whole (e.g. scope).

    A confirmation counts only if it verifies against `confirmer_key`; without a
    key, nothing counts as confirmed.
    """
    kept = [bucket for bucket in buckets if bucket.item_ids]
    empty = [
        CheckRun(
            check_name=bucket.check_name,
            check_version=bucket.check_version,
            parameters=bucket.parameters,
        )
        for bucket in buckets
        if not bucket.item_ids
    ]
    claimed = _check_conservation(ctx, kept)

    result = ctx.result
    explained = sum((bucket.amount for bucket in kept), Decimal(0))
    residual = result.gap - explained
    open_items = [item for item in ctx.items if item.id not in claimed]
    if sum((item.amount for item in open_items), Decimal(0)) != residual:
        raise ConservationError("buckets plus residual do not equal the gap")

    for assumption_id in extra_assumption_ids:
        if assumption_id not in ctx.assumptions:
            raise ConservationError(f"unknown assumption {assumption_id}")
    assumption_ids = list(
        dict.fromkeys([*(i for b in kept for i in b.assumption_ids), *extra_assumption_ids])
    )
    assumptions = ctx.assumptions.get_many(assumption_ids)
    unverified = {
        a.id: problem
        for a in assumptions
        if a.confirmed
        and (
            problem := confirmer_key.problem(a) if confirmer_key else "no confirmer key to check it"
        )
    }
    unconfirmed = [a.id for a in assumptions if not a.confirmed or a.id in unverified]
    source_warnings = [e.source_warning for e in (ctx.a.extract, ctx.b.extract) if e.source_warning]
    if open_items or residual != 0 or source_warnings:
        status = CertificateStatus.OPEN
    elif unconfirmed:
        status = CertificateStatus.PROVISIONAL
    else:
        status = CertificateStatus.RECONCILED

    draft = Certificate(
        id="",
        engine_version=__version__,
        status=status,
        created_at=now or datetime.now(UTC),
        extracts=[ctx.a.extract, ctx.b.extract],
        match_spec=ctx.spec,
        match=MatchSummary(
            matched_count=result.matched_count,
            only_a_count=len(result.only_a),
            only_b_count=len(result.only_b),
            difference_count=len(result.amount_differences),
            excluded_a_count=len(result.excluded_a),
            excluded_b_count=len(result.excluded_b),
            invalid_a_count=len(result.invalid_a),
            invalid_b_count=len(result.invalid_b),
            total_a=result.total_a,
            total_b=result.total_b,
            gap=result.gap,
        ),
        buckets=kept,
        checks_without_findings=empty,
        explained=explained,
        residual=residual,
        residual_items=[
            ResidualItem(
                id=item.id,
                kind=item.kind,
                key=list(item.key),
                amount=item.amount,
                row_ids=list(item.row_ids),
                reason=item.reason,
            )
            for item in open_items
        ],
        source_warnings=source_warnings,
        gross_unexplained=sum((abs(item.amount) for item in open_items), Decimal(0)),
        assumptions=assumptions,
        unconfirmed_assumptions=unconfirmed,
        unverified_confirmations=unverified,
        confirmer_key_fingerprint=confirmer_key.fingerprint if confirmer_key else None,
        confirmer=confirmer_key.owner if confirmer_key else None,
        supersedes=supersedes,
        certificate_hash="",
    )
    digest = certificate_hash(draft)
    return draft.model_copy(update={"id": f"cert_{digest[:16]}", "certificate_hash": digest})
