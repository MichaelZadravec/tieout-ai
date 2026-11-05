"""Local state: the workspace on disk, and one reconciliation in progress.

A Workspace is a directory (default `.recon/`, or $RECON_HOME) holding the
connections (with their credentials), the assumption store and issued
certificates. A Session is one reconciliation: two extracts, a MatchSpec, the
checks run so far, and certification.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from recon_engine.assumptions import AssumptionStore
from recon_engine.certify import certify, verify_certificate
from recon_engine.checks import CheckContext, ProposedBy, get_check
from recon_engine.connections import ConnectionStore
from recon_engine.match import match
from recon_engine.models import Assumption, Bucket, Certificate, ExtractData, MatchSpec


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
        cert = Certificate.model_validate_json(path.read_text(encoding="utf-8"))
        if not verify_certificate(cert) or cert.id != cert_id:
            raise CertificateError(f"Certificate '{cert_id}' does not match its hash")
        return cert

    def list_certificates(self) -> list[str]:
        if not self.certificate_dir.is_dir():
            return []
        return sorted(p.stem for p in self.certificate_dir.glob("cert_*.json"))


class Session:
    """One reconciliation. Checks run in order and never reclaim earlier claims.

    Each certificate issued by a session supersedes the one it issued before.
    """

    def __init__(
        self, workspace: Workspace, a: ExtractData, b: ExtractData, spec: MatchSpec
    ) -> None:
        self.workspace = workspace
        self.context = CheckContext(
            a=a, b=b, spec=spec, result=match(a, b, spec), assumptions=workspace.assumptions
        )
        self.buckets: list[Bucket] = []
        self.assumption_ids: list[str] = []
        self.last_certificate: str | None = None

    def run_check(
        self, name: str, params: dict[str, Any], proposed_by: ProposedBy = "ai"
    ) -> Bucket:
        bucket = get_check(name).run(self.context, params, proposed_by)
        self.context = self.context.with_claimed(bucket.item_ids)
        self.buckets.append(bucket)
        return bucket

    def propose_assumption(
        self, parameter: str, value: Any, description: str, proposed_by: ProposedBy = "ai"
    ) -> Assumption:
        """An assumption about the reconciliation as a whole (scope, filters, sources)."""
        assumption = self.workspace.assumptions.propose(
            Assumption.propose(parameter, value, description, proposed_by)
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
        )
        self.workspace.save_certificate(cert)
        self.last_certificate = cert.id
        return cert
