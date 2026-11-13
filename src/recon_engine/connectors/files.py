"""CSV and Excel files. The connection config names the file: {"path": "..."}.

For Excel the query is an optional sheet name (default: the first sheet). CSV
cells are read as text, exactly as written; empty cells become missing values.

The file's bytes are hashed and parsed from the same read, and the hash is
compared with the one recorded when a person registered the file. A file that
has changed since, or was never registered with a hash, is still read, but its
extract carries a warning and no certificate built on it can be better than OPEN.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from recon_engine.connectors.base import Connector, ConnectorError, build_extract
from recon_engine.hashing import sha256_hex
from recon_engine.models import ConnectionType, ExtractData

_EXCEL_SUFFIXES = {".xlsx", ".xlsm"}


class FileConnector(Connector):
    connection_type = ConnectionType.FILE

    def fetch(self, query: str | None = None) -> ExtractData:
        path = Path(self.connection.config["path"])
        if not path.is_file():
            raise ConnectorError(f"File for connection '{self.connection.id}' not found")
        suffix = path.suffix.lower()
        if suffix == ".csv":
            read = self._fetch_csv
        elif suffix in _EXCEL_SUFFIXES:
            read = self._fetch_excel
        else:
            raise ConnectorError(f"Unsupported file type '{suffix}'; expected .csv, .xlsx or .xlsm")
        content = path.read_bytes()
        data = read(path, content, query)
        changed, warning = self.registration_check(path, sha256_hex(content))
        extract = data.extract.model_copy(
            update={"changed_since_registration": changed, "source_warning": warning}
        )
        return ExtractData(extract=extract, frame=data.frame)

    def _fetch_csv(self, path: Path, content: bytes, sheet: str | None) -> ExtractData:
        if sheet is not None:
            raise ConnectorError("CSV files have no sheets; omit the query for a CSV file")
        frame = pd.read_csv(
            io.BytesIO(content), dtype=object, keep_default_na=False, encoding="utf-8-sig"
        )
        frame = frame.map(lambda v: None if v == "" else v)
        return build_extract(self.connection.id, f"FILE {path.name}", frame)

    def _fetch_excel(self, path: Path, content: bytes, sheet: str | None) -> ExtractData:
        with pd.ExcelFile(io.BytesIO(content), engine="openpyxl") as book:
            names = [str(n) for n in book.sheet_names]
            if sheet is None:
                sheet = names[0]
            elif sheet not in names:
                raise ConnectorError(f"Workbook {path.name} has no sheet '{sheet}'; has {names}")
            frame = pd.read_excel(book, sheet_name=sheet, dtype=object)
        return build_extract(self.connection.id, f"FILE {path.name} SHEET {sheet}", frame)
