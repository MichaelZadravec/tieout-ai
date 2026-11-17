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
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

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


def certificate_problems(cert: Certificate, confirmer_key: TrustedKey | None) -> list[str]:
    """Every way a certificate contradicts itself. Empty for a genuine certificate.

    The hash only shows a certificate was not altered after it was hashed, and
    anyone can recompute it. So a stored certificate must also add up: its
    buckets and residual must equal the gap, no item may be both explained and
    unexplained, its status must follow from its content, and every confirmation
    it counts must be signed (and verify, when this machine holds that key).
    """
    problems = []
    spec, match = cert.match_spec, cert.match
    if [e.id for e in cert.extracts] != [spec.extract_a, spec.extract_b]:
        problems.append("its extracts are not the ones its match spec names")
    if match.gap != match.total_a - match.total_b:
        problems.append(f"the gap {match.gap} is not total A minus total B")
    bucket_total = sum((b.amount for b in cert.buckets), Decimal(0))
    if cert.explained != bucket_total:
        problems.append(f"explained {cert.explained} is not the sum of the buckets {bucket_total}")
    if cert.explained + cert.residual != match.gap:
        problems.append(
            f"explained {cert.explained} plus residual {cert.residual} does not equal the gap "
            f"{match.gap}"
        )
    open_total = sum((i.amount for i in cert.residual_items), Decimal(0))
    if cert.residual != open_total:
        problems.append(
            f"residual {cert.residual} is not the sum of the unexplained items {open_total}"
        )
    gross = sum((abs(i.amount) for i in cert.residual_items), Decimal(0))
    if cert.gross_unexplained is not None and cert.gross_unexplained != gross:
        problems.append(
            f"gross unexplained {cert.gross_unexplained} is not the sum of the unexplained "
            f"items' absolute amounts {gross}"
        )

    open_ids = {i.id for i in cert.residual_items}
    owner: dict[str, str] = {}
    listed = {a.id for a in cert.assumptions}
    for bucket in cert.buckets:
        for item_id in bucket.item_ids:
            if item_id in open_ids:
                problems.append(f"item {item_id} is both explained and unexplained")
            if item_id in owner:
                problems.append(f"item {item_id} is in more than one bucket")
            owner[item_id] = bucket.check_name
        for assumption_id in bucket.assumption_ids:
            if assumption_id not in listed:
                problems.append(
                    f"bucket '{bucket.check_name}' cites assumption {assumption_id}, which the "
                    "certificate does not list"
                )

    unconfirmed = set(cert.unconfirmed_assumptions)
    for assumption_id in unconfirmed - listed:
        problems.append(f"unconfirmed assumption {assumption_id} is not listed")
    for assumption_id in set(cert.unverified_confirmations) - unconfirmed:
        problems.append(f"assumption {assumption_id} could not be verified but counts as confirmed")
    for a in cert.assumptions:
        if a.id in unconfirmed:
            continue
        if not a.confirmed:
            problems.append(f"assumption {a.id} is not confirmed but not listed as unconfirmed")
        elif cert.confirmer_key_fingerprint is not None:
            if not a.signature:
                problems.append(f"the confirmation of {a.id} is not signed")
            elif a.signing_key_fingerprint != cert.confirmer_key_fingerprint:
                problems.append(
                    f"the confirmation of {a.id} was signed with a different key from the one "
                    "the certificate names"
                )
            elif confirmer_key and confirmer_key.fingerprint == cert.confirmer_key_fingerprint:
                problem = confirmer_key.problem(a)
                if problem:
                    problems.append(f"the confirmation of {a.id} does not verify: {problem}")

    if cert.residual_items or cert.residual != 0 or cert.source_warnings:
        expected = CertificateStatus.OPEN
    elif unconfirmed:
        expected = CertificateStatus.PROVISIONAL
    else:
        expected = CertificateStatus.RECONCILED
    if cert.status != expected:
        problems.append(f"status {cert.status} does not follow from its content (it is {expected})")
    return problems


SignOffState = Literal["signed off", "not signed off", "sign-off does not verify"]


@dataclass(frozen=True)
class SignOff:
    """Whether a person signed a certificate off, and who, when and with which key."""

    status: SignOffState
    by: str | None = None
    at: str | None = None
    fingerprint: str | None = None
    problem: str | None = None

    @property
    def signed(self) -> bool:
        return self.status == "signed off"


def sign_off_record(cert: Certificate) -> dict[str, str]:
    """What a sign-off vouches for: this exact certificate, by hash, in this status."""
    return {
        "certificate_id": cert.id,
        "certificate_hash": cert.certificate_hash,
        "status": cert.status.value,
    }


def check_sign_off(
    cert: Certificate, stored: dict[str, Any] | None, key: TrustedKey | None
) -> SignOff:
    """Verify a stored sign-off against the certificate and this machine's confirmer key."""
    if stored is None:
        return SignOff("not signed off")
    signature = stored.get("signature") or {}
    record = {k: stored.get(k) for k in ("certificate_id", "certificate_hash", "status")}
    if record != sign_off_record(cert):
        problem = "the sign-off was made for a different certificate"
    elif key is None:
        problem = "there is no confirmer key on this machine to check the sign-off"
    else:
        found = key.record_problem("certificate", record, signature)
        problem = f"the sign-off {found}" if found else None
    return SignOff(
        "sign-off does not verify" if problem else "signed off",
        by=signature.get("by"),
        at=signature.get("at"),
        fingerprint=signature.get("fingerprint"),
        problem=problem,
    )


def confirmation_status(cert: Certificate, confirmer_key: TrustedKey | None = None) -> str | None:
    """Why this machine cannot vouch for the certificate's confirmations, or None if it can."""
    counted = [
        a for a in cert.assumptions if a.confirmed and a.id not in cert.unconfirmed_assumptions
    ]
    if not counted:
        return None
    if cert.confirmer_key_fingerprint is None:
        return "issued before confirmations were signed: its confirmations cannot be verified"
    if confirmer_key is None or confirmer_key.fingerprint != cert.confirmer_key_fingerprint:
        return (
            f"its confirmations were signed with key {cert.confirmer_key_fingerprint}, which is "
            "not this machine's confirmer key, so they cannot be verified here"
        )
    return None


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
