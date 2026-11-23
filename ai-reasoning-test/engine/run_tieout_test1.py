"""Run AI TieOut on the Test 1 (ambiguity) files and print what it can prove.

    pip install tieout-ai
    python run_tieout_test1.py

This does what `tieout demo` does, on the Test 1 files: it registers both CSVs, runs the
same six checks, confirms the assumptions with a throwaway key made in a temporary
folder (your own key is never touched), and prints the certificate's result.
No AI is involved: this is the deterministic engine on its own.
"""

import os
import secrets
import shutil
import tempfile
from pathlib import Path

from tieout_ai.connections import file_registration, sign_connection
from tieout_ai.demo import MATCH
from tieout_ai.models import Connection, ConnectionType
from tieout_ai.render import render_html
from tieout_ai.service import ReconService
from tieout_ai.session import Workspace
from tieout_ai.signing import init_keys, load_signer

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data" / "test1-ambiguity"
FILES = {"dashboard": "dashboard_nov2026.csv", "xero": "xero_export_nov.csv"}
PLAN = [
    ("timing", {"max_shift_days": 30}),
    ("status_filter", {"side": "a", "status_column": "status", "statuses": ["VOIDED"]}),
    ("tax_basis", {"inclusive_side": "b", "rate": "0.10", "rounding_tolerance": "0.01"}),
    (
        "duplicates",
        {
            "side": "a",
            "compare_columns": ["invoice_number", "appointment_date", "amount", "status", "type"],
        },
    ),
    ("sign_credit", {"mode": "sign_flip"}),
    ("rounding", {"tolerance": "0.01"}),
]


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="tieout-test1-"))
    os.environ["TIEOUT_KEYS_DIR"] = str(work / "keys")
    passphrase = secrets.token_urlsafe(16)
    init_keys("Test 1 throwaway key", passphrase)
    person = load_signer(passphrase)

    data = work / "data"
    data.mkdir()
    ws = Workspace(work / ".tieout")
    for conn_id, name in FILES.items():
        path = data / name
        shutil.copy(DATA / name, path)
        reg = {**file_registration(path), "registered_by": person.owner, "registered_at": "test1"}
        config = {"path": str(path), "registered": reg}
        conn = Connection(id=conn_id, type=ConnectionType.FILE, config=config)
        ws.connections.add(sign_connection(conn, person), replace=True)

    svc = ReconService(ws)
    a = svc.run_query("dashboard")["extract_id"]
    b = svc.run_query("xero")["extract_id"]
    period = {"period_start": "2026-11-01", "period_end": "2026-11-30"}
    rec = svc.match(a, b, **MATCH, **period)["reconciliation_id"]
    for name, params in PLAN:
        svc.run_check(rec, name, params)
    for assumption in ws.assumptions.all():
        ws.assumptions.confirm(assumption.id, person)

    cert = ws.load_certificate(svc.certify(rec)["certificate_id"])
    print(f"Status: {cert.status.value}")
    print(f"Gap: {cert.match.gap}")
    print("Proven:")
    for bucket in cert.buckets:
        print(f"  {bucket.check_name:<14} {bucket.amount:>10}  ({bucket.row_count} rows)")
    print(f"Unexplained (net): {cert.residual}")
    for item in cert.residual_items:
        print(f"  {item.kind:<11} {item.key} {item.amount}")
    html = work / "certificate.html"
    html.write_text(render_html(cert, ws.sign_off(cert)), encoding="utf-8")
    print(f"\nCertificate: {html}")


if __name__ == "__main__":
    main()
