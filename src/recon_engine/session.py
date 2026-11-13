"""Local state: the workspace on disk, and one reconciliation in progress.

A Workspace is a directory (default `.recon/`, or $RECON_HOME) holding the
connections (with their credentials), the assumption store and issued
certificates. A Session is one reconciliation: two extracts, a MatchSpec, the
checks run so far, and certification.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from recon_engine.assumptions import AssumptionStore
from recon_engine.certify import certify, verify_certificate_document
from recon_engine.checks import CheckContext, ProposedBy, get_check
from recon_engine.checks.base import Side
from recon_engine.connections import ConnectionStore
from recon_engine.match import match
from recon_engine.models import (
    Assumption,
    Bucket,
    Certificate,
    ExtractData,
    MatchSpec,
    QueryFilter,
)
from recon_engine.signing import load_trusted_key


class CertificateError(Exception):
    """A certificate is missing or has been altered."""


def default_home() -> Path:
    return Path(os.environ.get("RECON_HOME") or ".recon")


class Workspace:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_home()
        self.assumptions = AssumptionStore(self.root / "assumptions.json")
        self.connections = ConnectionStore(self.root / "connections.json")

    @property
    def certificate_dir(self) -> Path:
        return self.root / "certificates"

    def save_certificate(self, cert: Certificate) -> Path:
        self.certificate_dir.mkdir(parents=True, exist_ok=True)
        path = self.certificate_dir / f"{cert.id}.json"
        path.write_text(cert.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
        return path

    def load_certificate(self, cert_id: str) -> Certificate:
        path = self.certificate_dir / f"{cert_id}.json"
        if not path.is_file():
            raise CertificateError(f"No certificate '{cert_id}' in {self.root}")
        document = json.loads(path.read_text(encoding="utf-8"))
        if not verify_certificate_document(document) or document.get("id") != cert_id:
            raise CertificateError(f"Certificate '{cert_id}' does not match its hash")
        return Certificate.model_validate(document)

    def list_certificates(self) -> list[str]:
        if not self.certificate_dir.is_dir():
            return []
        return sorted(p.stem for p in self.certificate_dir.glob("cert_*.json"))


class Session:
    """One reconciliation. Checks run in order and never reclaim earlier claims.

    Each certificate issued by a session supersedes the one it issued before.

    Every way a SQL extract's query leaves rows out becomes an assumption of the
    reconciliation as soon as it starts: a filter only to the reconciliation
    period is a normal one, anything else is elevated. Either way a person must
    confirm it before the certificate can be RECONCILED.
    """

    def __init__(
        self,
        workspace: Workspace,
        a: ExtractData,
        b: ExtractData,
        spec: MatchSpec,
        proposed_by: ProposedBy = "ai",
    ) -> None:
        self.workspace = workspace
        self.context = CheckContext(
            a=a, b=b, spec=spec, result=match(a, b, spec), assumptions=workspace.assumptions
        )
        self.buckets: list[Bucket] = []
        self.assumption_ids: list[str] = []
        self.last_certificate: str | None = None
        for side in ("a", "b"):
            for query_filter in self.context.data(side).extract.query_filters:
                self._propose_filter(side, query_filter, proposed_by)

    def _propose_filter(
        self, side: Side, query_filter: QueryFilter, proposed_by: ProposedBy
    ) -> None:
        extract = self.context.data(side).extract
        name = self.context.name(side)
        period = self.context.spec.period
        to_period = (
            query_filter.clause == "WHERE"
            and period is not None
            and query_filter.date_column is not None
            and query_filter.date_column == self.context.date_column(side)
            and (query_filter.earliest is None or query_filter.earliest <= period.start)
            and (query_filter.latest is None or query_filter.latest >= period.end)
        )
        what = {
            "WHERE": f"keeps only rows where {query_filter.plain}",
            "HAVING": f"keeps only groups where {query_filter.plain}",
        }.get(query_filter.clause, query_filter.plain)
        description = f"The {name} query {what}. SQL: {query_filter.sql}"
        if to_period:
            description += " (this only limits it to the reconciliation period)"
        self.propose_assumption(
            "query.filter",
            {
                "connection": extract.connection_id,
                "query_hash": extract.query_hash,
                "clause": query_filter.clause,
                "sql": query_filter.sql,
            },
            description,
            proposed_by,
            elevated_reason=None
            if to_period
            else f"loosens the default: the {name} query leaves out rows beyond the "
            f"reconciliation period ({query_filter.clause} {query_filter.sql})",
        )

    def run_check(
        self, name: str, params: dict[str, Any], proposed_by: ProposedBy = "ai"
    ) -> Bucket:
        bucket = get_check(name).run(self.context, params, proposed_by)
        self.context = self.context.with_claimed(bucket.item_ids)
        self.buckets.append(bucket)
        return bucket

    def propose_assumption(
        self,
        parameter: str,
        value: Any,
        description: str,
        proposed_by: ProposedBy = "ai",
        elevated_reason: str | None = None,
    ) -> Assumption:
        """An assumption about the reconciliation as a whole (scope, filters, sources)."""
        assumption = self.workspace.assumptions.propose(
            Assumption.propose(
                parameter, value, description, proposed_by, elevated_reason=elevated_reason
            )
        )
        if assumption.id not in self.assumption_ids:
            self.assumption_ids.append(assumption.id)
        return assumption

    def certify(self, supersedes: str | None = None) -> Certificate:
        if supersedes is None:
            supersedes = self.last_certificate
        else:
            self.workspace.load_certificate(supersedes)  # must exist and verify
        cert = certify(
            self.context,
            self.buckets,
            supersedes=supersedes,
            extra_assumption_ids=self.assumption_ids,
            confirmer_key=load_trusted_key(),
        )
        self.workspace.save_certificate(cert)
        self.last_certificate = cert.id
        return cert
