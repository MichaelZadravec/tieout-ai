"""`recon demo`: the clean and messy fixture pairs, end to end, in a folder of their own.

It does what a person and an AI would do together: generates both "dashboard vs
Xero" pairs, registers and signs the two sources, runs the analyst's plan through
the same service the MCP server uses, confirms the assumptions, certifies, and signs
off the clean result. The clean pair reconciles; the messy pair stays honestly OPEN.

Confirming and signing need a key, so the demo makes a **throwaway** one inside the
demo folder, named "Demo confirmer (throwaway key)". The person's real key is never
read or touched, and every demo certificate shows the throwaway key's fingerprint.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from recon_engine.connections import file_registration, sign_connection
from recon_engine.fixtures import generate, generate_messy
from recon_engine.models import Connection, ConnectionType
from recon_engine.render import render_html
from recon_engine.service import ReconService
from recon_engine.session import Workspace
from recon_engine.signing import init_keys, load_signer

DEMO_CONFIRMER = "Demo confirmer (throwaway key)"
MATCH = {
    "key_columns": ["invoice_number"],
    "key_columns_b": ["InvoiceNumber"],
    "amount_column": "amount",
    "amount_column_b": "Total",
    "date_column": "appointment_date",
    "date_column_b": "InvoiceDate",
}
CASES = {
    "clean": (generate, generate.PLAN),
    "messy": (generate_messy, generate_messy.PLAN),
}


@dataclass(frozen=True)
class DemoResult:
    case: str
    certificate_id: str
    status: str
    signed_off: bool
    gap: Decimal
    residual: Decimal
    workspace: Path
    html: Path


@contextmanager
def _keys_in(directory: Path) -> Iterator[None]:
    """Point the engine at the demo's own key folder, and put things back after."""
    previous = os.environ.get("RECON_KEYS_DIR")
    os.environ["RECON_KEYS_DIR"] = str(directory)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("RECON_KEYS_DIR", None)
        else:
            os.environ["RECON_KEYS_DIR"] = previous


def run_demo(out_dir: Path) -> list[DemoResult]:
    """Run both cases under `out_dir` and return what each certificate says."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with _keys_in(out_dir / "keys"):
        passphrase = secrets.token_urlsafe(24)  # used once, here, and never stored
        init_keys(DEMO_CONFIRMER, passphrase)
        person = load_signer(passphrase)
        for case, (module, plan) in CASES.items():
            data = out_dir / case / "data"
            summary = (module.generate if case == "clean" else module.generate_messy)(data)
            workspace = Workspace(out_dir / case / ".recon")
            for conn_id, name in (("dashboard", module.DASHBOARD_FILE), ("xero", module.XERO_FILE)):
                path = data / name
                registered = {
                    **file_registration(path),
                    "registered_by": person.owner,
                    "registered_at": "demo",
                }
                connection = Connection(
                    id=conn_id,
                    type=ConnectionType.FILE,
                    config={"path": str(path), "registered": registered},
                )
                workspace.connections.add(sign_connection(connection, person), replace=True)

            service = ReconService(workspace)
            a = service.run_query("dashboard")["extract_id"]
            b = service.run_query("xero")["extract_id"]
            period = summary["period"]
            rec = service.match(
                a, b, **MATCH, period_start=period["start"], period_end=period["end"]
            )["reconciliation_id"]
            for name, params in plan:
                service.run_check(rec, name, params)
            for assumption in workspace.assumptions.all():
                workspace.assumptions.confirm(assumption.id, person)

            issued = service.certify(rec)
            cert = workspace.load_certificate(issued["certificate_id"])
            if cert.status.value == "RECONCILED":
                workspace.save_sign_off(cert, person)
            sign_off = workspace.sign_off(cert)
            html = workspace.certificate_dir / f"{cert.id}.html"
            html.write_text(render_html(cert, sign_off), encoding="utf-8")
            results.append(
                DemoResult(
                    case=case,
                    certificate_id=cert.id,
                    status=cert.status.value,
                    signed_off=sign_off.signed,
                    gap=cert.match.gap,
                    residual=cert.residual,
                    workspace=workspace.root,
                    html=html,
                )
            )
    (out_dir / "README.txt").write_text(
        "recon demo output. Certificates are signed with a throwaway key made for the "
        "demo; they are not anyone's real confirmations.\n"
        + json.dumps({r.case: r.certificate_id for r in results}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return results
