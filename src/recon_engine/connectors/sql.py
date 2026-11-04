"""SQL databases via SQLAlchemy. The connection config holds the URL: {"url": "..."}.

Two layers keep this read-only:
1. Every query is parsed with sqlglot and rejected unless it is a single SELECT
   (or set operation of SELECTs) with no writing or locking clause anywhere inside.
2. The session itself is read-only where the engine can enforce it (SQLite's
   query_only pragma) and is always rolled back. For server databases, configure
   the connection with a read-only database user.
"""

from __future__ import annotations

import pandas as pd
import sqlglot
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp
from sqlglot.errors import SqlglotError

from recon_engine.connectors.base import (
    Connector,
    ConnectorError,
    UnsafeQueryError,
    build_extract,
)
from recon_engine.models import ConnectionType, ExtractData

# SQLAlchemy backend name -> sqlglot dialect.
_DIALECTS = {"sqlite": "sqlite", "postgresql": "postgres", "mssql": "tsql", "mysql": "mysql"}

_READ_ONLY_ROOTS = (exp.Select, exp.SetOperation)

# Nodes that write, change schema or take locks, wherever they appear in the tree
# (e.g. SELECT ... INTO, FOR UPDATE, or a Postgres data-modifying CTE).
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Into,
    exp.Lock,
)


def _label(node: exp.Expression) -> str:
    return node.key.upper()


def validate_select(sql: str, dialect: str | None = None) -> exp.Expression:
    """Return the parsed query, or raise UnsafeQueryError explaining why it is refused."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("Query is empty")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as error:
        first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
        raise UnsafeQueryError(f"Query could not be parsed as SQL: {first_line}") from None
    if len(statements) != 1:
        raise UnsafeQueryError(
            f"Only a single SELECT statement is allowed; found {len(statements)} statements"
        )
    [statement] = statements
    if not isinstance(statement, _READ_ONLY_ROOTS):
        raise UnsafeQueryError(f"Only SELECT queries are allowed; got {_label(statement)}")
    for node in statement.walk():
        if isinstance(node, _FORBIDDEN):
            raise UnsafeQueryError(
                f"SELECT contains a forbidden {_label(node)} clause; queries must be read-only"
            )
    return statement


class SQLConnector(Connector):
    connection_type = ConnectionType.SQL

    def fetch(self, query: str | None = None) -> ExtractData:
        if query is None:
            raise ConnectorError("SQL connections need a query")
        url = make_url(self.connection.config["url"])
        backend = url.get_backend_name()
        validate_select(query, _DIALECTS.get(backend))

        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                if backend == "sqlite":
                    conn.exec_driver_sql("PRAGMA query_only = ON")
                result = conn.exec_driver_sql(query)
                columns = list(result.keys())
                rows = [tuple(r) for r in result.fetchall()]
                conn.rollback()
        except SQLAlchemyError as error:
            # Report the driver's message only: SQLAlchemy's own text can include the URL.
            detail = getattr(error, "orig", None) or type(error).__name__
            raise ConnectorError(
                f"Query failed on connection '{self.connection.id}': {detail}"
            ) from None
        finally:
            engine.dispose()

        frame = pd.DataFrame(rows, columns=columns, dtype=object)
        return build_extract(self.connection.id, query, frame)
