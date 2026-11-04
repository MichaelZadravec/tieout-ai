"""CSV and Excel files. The connection config names the file: {"path": "..."}.

For Excel the query is an optional sheet name (default: the first sheet). CSV
cells are read as text, exactly as written; empty cells become missing values.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from recon_engine.connectors.base import Connector, ConnectorError, build_extract
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
            return self._fetch_csv(path, query)
        if suffix in _EXCEL_SUFFIXES:
            return self._fetch_excel(path, query)
        raise ConnectorError(f"Unsupported file type '{suffix}'; expected .csv, .xlsx or .xlsm")

    def _fetch_csv(self, path: Path, sheet: str | None) -> ExtractData:
        if sheet is not None:
            raise ConnectorError("CSV files have no sheets; call fetch() without a query")
        frame = pd.read_csv(path, dtype=object, keep_default_na=False, encoding="utf-8-sig")
        frame = frame.map(lambda v: None if v == "" else v)
        return build_extract(self.connection.id, f"FILE {path.name}", frame)

    def _fetch_excel(self, path: Path, sheet: str | None) -> ExtractData:
        with pd.ExcelFile(path, engine="openpyxl") as book:
            names = [str(n) for n in book.sheet_names]
            if sheet is None:
                sheet = names[0]
            elif sheet not in names:
                raise ConnectorError(f"Workbook {path.name} has no sheet '{sheet}'; has {names}")
            frame = pd.read_excel(book, sheet_name=sheet, dtype=object)
        return build_extract(self.connection.id, f"FILE {path.name} SHEET {sheet}", frame)
