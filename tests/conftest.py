import csv
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from support import COLUMNS, CONFIRMER, INVOICES, PASSPHRASE


@pytest.fixture
def invoices_csv(tmp_path: Path) -> Path:
    path = tmp_path / "invoices.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(INVOICES)
    return path


@pytest.fixture
def invoices_db(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE invoices (invoice_id TEXT, invoice_date TEXT, amount REAL, status TEXT)"
        )
        db.executemany(
            "INSERT INTO invoices VALUES (?, ?, ?, ?)",
            [(i, d, float(a), s) for i, d, a, s in INVOICES],
        )
    return path


@pytest.fixture(scope="session")
def confirmer_keys(tmp_path_factory) -> Path:
    from recon_engine.signing import init_keys

    directory = tmp_path_factory.mktemp("keys")
    init_keys(CONFIRMER, PASSPHRASE, directory)
    return directory


@pytest.fixture(autouse=True)
def isolated_keys(confirmer_keys, monkeypatch):
    """Every test signs with the test confirmer key, never a real one."""
    monkeypatch.setenv("RECON_KEYS_DIR", str(confirmer_keys))
