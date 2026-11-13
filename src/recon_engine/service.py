"""The engine's operations as plain-JSON calls: what the MCP server exposes.

Every input is a reference (connection, extract, reconciliation or certificate
ID) or a parameter; no call accepts data values. Every number in an output is
computed here, so an AI's explanation can be traced to the engine.

Deliberately absent: confirming an assumption. That is human-only (the CLI).
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from recon_engine.checks import list_checks as registered_checks
from recon_engine.connections import ConnectionStoreError
from recon_engine.connectors import connector_for
from recon_engine.hashing import canonical_value, to_decimal
from recon_engine.models import Certificate, Extract, ExtractData, MatchSpec, Period
from recon_engine.render import render_html
from recon_engine.session import Session, Workspace

MAX_PAGE = 200


class ServiceError(Exception):
    """A request refers to something that does not exist, or asks for too much."""


def _json(value: Any) -> Any:
    """Make a value JSON-safe. Money stays exact: Decimals become strings."""
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json(v) for v in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _extract_summary(extract: Extract) -> dict[str, Any]:
    return _json(
        {
            "extract_id": extract.id,
            "connection_id": extract.connection_id,
            "query_text": extract.query_text,
            "query_hash": extract.query_hash,
            "columns": extract.columns,
            "row_count": extract.row_count,
            "column_totals": extract.column_totals,
            "data_hash": extract.data_hash,
            "fetched_at": extract.fetched_at,
        }
    )


def _item(item) -> dict[str, Any]:
    return _json(
        {
            "id": item.id,
            "kind": item.kind,
            "key": list(item.key),
            "amount": item.amount,
            "amount_a": item.amount_a,
            "amount_b": item.amount_b,
            "row_ids": list(item.row_ids),
            "reason": item.reason,
        }
    )


class ReconService:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.extracts: dict[str, ExtractData] = {}
        self.sessions: dict[str, Session] = {}

    # --- lookups -----------------------------------------------------------------

    def _extract(self, extract_id: str) -> ExtractData:
        try:
            return self.extracts[extract_id]
        except KeyError:
            raise ServiceError(
                f"No extract '{extract_id}' in this server session; run run_query first"
            ) from None

    def _session(self, reconciliation_id: str) -> Session:
        try:
            return self.sessions[reconciliation_id]
        except KeyError:
            raise ServiceError(
                f"No reconciliation '{reconciliation_id}' in this server session; run match first"
            ) from None

    def _status(self, session: Session) -> dict[str, Any]:
        ctx = session.context
        open_items = ctx.open_items()
        explained = sum((b.amount for b in session.buckets), Decimal(0))
        return _json(
            {
                "gap": ctx.result.gap,
                "explained_so_far": explained,
                "residual": ctx.result.gap - explained,
                "gross_unexplained": sum((abs(i.amount) for i in open_items), Decimal(0)),
                "open_items": len(open_items),
                "open_items_by_kind": dict(Counter(i.kind for i in open_items)),
            }
        )

    # --- sources -----------------------------------------------------------------

    def list_connections(self) -> list[dict[str, str]]:
        return [{"id": c.id, "type": c.type.value} for c in self.workspace.connections.all()]

    def run_query(self, connection_id: str, query: str | None = None) -> dict[str, Any]:
        try:
            connection = self.workspace.connections.get(connection_id)
        except ConnectionStoreError as error:
            raise ServiceError(str(error)) from None
        data = connector_for(connection).fetch(query)
        self.extracts[data.extract.id] = data
        return _extract_summary(data.extract)

    def profile_source(self, connection_id: str, query: str | None = None) -> dict[str, Any]:
        summary = self.run_query(connection_id, query)
        frame = self.extracts[summary["extract_id"]].frame
        profiles = []
        for column in frame.columns:
            values = [canonical_value(v) for v in frame[column]]
            present = [v for v in values if v is not None]
            counts = Counter(present)
            profile: dict[str, Any] = {
                "column": column,
                "non_empty": len(present),
                "empty": len(values) - len(present),
                "distinct": len(counts),
            }
            numbers = []
            for value in present:
                try:
                    numbers.append(to_decimal(value))
                except ValueError:
                    numbers = []
                    break
            if numbers and not any(isinstance(v, bool) for v in present):
                profile.update(min=min(numbers), max=max(numbers), sum=sum(numbers))
            elif present:
                as_text = sorted(map(str, present))
                profile.update(min=as_text[0], max=as_text[-1])
            if 0 < len(counts) <= 20:
                profile["values"] = {str(k): n for k, n in counts.most_common()}
            profiles.append(profile)
        return {**summary, "profile": _json(profiles)}

    def get_rows(
        self,
        extract_id: str,
        offset: int = 0,
        limit: int = 50,
        row_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        data = self._extract(extract_id)
        if limit < 1 or limit > MAX_PAGE:
            raise ServiceError(f"limit must be between 1 and {MAX_PAGE}")
        if offset < 0:
            raise ServiceError("offset must not be negative")
        frame = data.frame
        if row_ids is not None:
            if len(row_ids) > MAX_PAGE:
                raise ServiceError(f"At most {MAX_PAGE} row_ids per call")
            ordinals = []
            for row_id in row_ids:
                prefix, _, ordinal = row_id.rpartition(":")
                if prefix != extract_id or not ordinal.isdigit() or int(ordinal) >= len(frame):
                    raise ServiceError(f"Row '{row_id}' is not in extract {extract_id}")
                ordinals.append(int(ordinal))
        else:
            ordinals = list(range(offset, min(offset + limit, len(frame))))
        rows = [
            {
                "row_id": f"{extract_id}:{n}",
                "values": {
                    col: canonical_value(val)
                    for col, val in zip(frame.columns, frame.iloc[n].tolist(), strict=True)
                },
            }
            for n in ordinals
        ]
        return {"extract_id": extract_id, "total_rows": len(frame), "offset": offset, "rows": rows}

    # --- reconciliation ----------------------------------------------------------

    def match(
        self,
        extract_a: str,
        extract_b: str,
        key_columns: list[str],
        amount_column: str,
        key_columns_b: list[str] | None = None,
        amount_column_b: str | None = None,
        date_column: str | None = None,
        date_column_b: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> dict[str, Any]:
        a, b = self._extract(extract_a), self._extract(extract_b)
        if (period_start is None) != (period_end is None):
            raise ServiceError("Give both period_start and period_end, or neither")
        spec = MatchSpec(
            extract_a=extract_a,
            extract_b=extract_b,
            key_columns=key_columns,
            amount_column=amount_column,
            key_columns_b=key_columns_b,
            amount_column_b=amount_column_b,
            date_column=date_column,
            date_column_b=date_column_b,
            period=Period(start=period_start, end=period_end) if period_start else None,
        )
        session = Session(self.workspace, a, b, spec)
        reconciliation_id = f"rec_{uuid.uuid4().hex[:10]}"
        self.sessions[reconciliation_id] = session
        result = session.context.result
        return {
            "reconciliation_id": reconciliation_id,
            "a": session.context.name("a"),
            "b": session.context.name("b"),
            **_json(
                {
                    "total_a": result.total_a,
                    "total_b": result.total_b,
                    "matched_keys": result.matched_count,
                    "only_a_rows": len(result.only_a),
                    "only_b_rows": len(result.only_b),
                    "keys_with_amount_differences": len(result.amount_differences),
                    "rows_outside_period": {
                        "a": len(result.excluded_a),
                        "b": len(result.excluded_b),
                    },
                    "unusable_rows": {"a": len(result.invalid_a), "b": len(result.invalid_b)},
                }
            ),
            **self._status(session),
        }

    def list_open_items(
        self,
        reconciliation_id: str,
        kind: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        session = self._session(reconciliation_id)
        if limit < 1 or limit > MAX_PAGE:
            raise ServiceError(f"limit must be between 1 and {MAX_PAGE}")
        items = [i for i in session.context.open_items() if kind is None or i.kind == kind]
        page = items[offset : offset + limit]
        return {
            "reconciliation_id": reconciliation_id,
            "total": len(items),
            "offset": offset,
            "items": [_item(i) for i in page],
        }

    def list_checks(self) -> list[dict[str, Any]]:
        return [
            {
                "name": check.name,
                "version": check.version,
                "description": check.description,
                "parameters": check.Params.model_json_schema(),
            }
            for check in registered_checks()
        ]

    def run_check(
        self, reconciliation_id: str, check: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        session = self._session(reconciliation_id)
        bucket = session.run_check(check, params, proposed_by="ai")
        assumptions = self.workspace.assumptions.get_many(bucket.assumption_ids)
        return {
            "check": bucket.check_name,
            "version": bucket.check_version,
            "found": bool(bucket.item_ids),
            **_json(
                {
                    "items_claimed": len(bucket.item_ids),
                    "rows_claimed": bucket.row_count,
                    "amount": bucket.amount,
                    "evidence_rows": len(bucket.evidence_row_ids),
                    "assumptions": [
                        {
                            "id": a.id,
                            "description": a.description,
                            "elevated": a.elevated,
                            "elevated_reason": a.elevated_reason,
                            "confirmed": a.confirmed,
                        }
                        for a in assumptions
                    ]
                    if bucket.item_ids
                    else [],
                }
            ),
            "after_this_check": self._status(session),
        }

    def propose_assumption(
        self, reconciliation_id: str, parameter: str, value: Any, description: str
    ) -> dict[str, Any]:
        session = self._session(reconciliation_id)
        assumption = session.propose_assumption(parameter, value, description, proposed_by="ai")
        return {
            "id": assumption.id,
            "description": assumption.description,
            "confirmed": assumption.confirmed,
            "note": "Only a person can confirm this, with `recon confirm` in a terminal.",
        }

    def certify(self, reconciliation_id: str, supersedes: str | None = None) -> dict[str, Any]:
        session = self._session(reconciliation_id)
        cert = session.certify(supersedes=supersedes)
        html_path = self.workspace.certificate_dir / f"{cert.id}.html"
        html_path.write_text(render_html(cert), encoding="utf-8")
        return {
            **self._certificate_summary(cert),
            "json_path": str(self.workspace.certificate_dir / f"{cert.id}.json"),
            "html_path": str(html_path),
        }

    def get_certificate(self, certificate_id: str) -> dict[str, Any]:
        cert = self.workspace.load_certificate(certificate_id)  # verifies the hash
        return {**self._certificate_summary(cert), "hash_verified": True}

    def _certificate_summary(self, cert: Certificate) -> dict[str, Any]:
        unconfirmed = [a for a in cert.assumptions if not a.confirmed]
        next_step = {
            "RECONCILED": "Nothing further: every item is explained and every "
            "assumption confirmed.",
            "PROVISIONAL": "The gap is explained but rests on unconfirmed assumptions. A person "
            "must confirm each with `recon confirm <id>` in a terminal; then re-certify.",
            "OPEN": "Some differences are unexplained. Inspect them with list_open_items, try "
            "further checks, or report them as unexplained.",
        }[cert.status.value]
        return _json(
            {
                "certificate_id": cert.id,
                "status": cert.status.value,
                "supersedes": cert.supersedes,
                "total_a": cert.match.total_a,
                "total_b": cert.match.total_b,
                "gap": cert.match.gap,
                "explained": cert.explained,
                "residual": cert.residual,
                "gross_unexplained": cert.gross,
                "unexplained_items": len(cert.residual_items),
                "buckets": [
                    {
                        "check": b.check_name,
                        "version": b.check_version,
                        "rows": b.row_count,
                        "amount": b.amount,
                    }
                    for b in cert.buckets
                ],
                "checks_without_findings": [r.check_name for r in cert.checks_without_findings],
                "unconfirmed_assumptions": [
                    {"id": a.id, "description": a.description, "elevated": a.elevated}
                    for a in unconfirmed
                ],
                "elevated_assumptions": [
                    {"id": a.id, "reason": a.elevated_reason, "confirmed": a.confirmed}
                    for a in cert.assumptions
                    if a.elevated
                ],
                "certificate_hash": cert.certificate_hash,
                "next_step": next_step,
            }
        )
