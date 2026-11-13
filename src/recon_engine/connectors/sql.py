"""SQL databases via SQLAlchemy. The connection config holds the URL: {"url": "..."}.

Two layers keep this read-only:
1. Every query is parsed with sqlglot and rejected unless it is a single SELECT
   (or set operation of SELECTs) with no writing or locking clause anywhere inside.
2. The session itself is read-only where the engine can enforce it (SQLite's
   query_only pragma) and is always rolled back. For server databases, configure
   the connection with a read-only database user.

Queries must also return every row they select: LIMIT, TOP, OFFSET, FETCH,
SAMPLE and TABLESAMPLE are refused. Every other way a query leaves rows out
(WHERE and HAVING conditions, JOINs, DISTINCT, UNION, EXCEPT, INTERSECT) is
recorded on the extract (`describe_filters`) and becomes an assumption that a
person must confirm.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

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
from recon_engine.models import ConnectionType, ExtractData, QueryFilter

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


# Nodes that return only some of the selected rows.
_TRUNCATING = (exp.Limit, exp.Fetch, exp.Offset, exp.TableSample)


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
        if isinstance(node, _TRUNCATING):
            raise UnsafeQueryError(
                f"SELECT contains {_label(node)}, which would truncate the data; queries must "
                "return every row they select"
            )
    return statement


_WORDS = {
    exp.EQ: "is",
    exp.NEQ: "is not",
    exp.GT: "is greater than",
    exp.GTE: "is at least",
    exp.LT: "is less than",
    exp.LTE: "is at most",
}


def _conjuncts(condition: exp.Expression) -> list[exp.Expression]:
    """The AND-ed parts of a condition, each one a separate filter."""
    if isinstance(condition, exp.Paren):
        return _conjuncts(condition.this)
    if isinstance(condition, exp.And):
        return _conjuncts(condition.left) + _conjuncts(condition.right)
    return [condition]


def _plain(e: exp.Expression, dialect: str | None) -> str:
    """A condition in words, falling back to its SQL for anything unusual."""

    def sql(node: exp.Expression) -> str:
        return node.sql(dialect=dialect)

    if isinstance(e, exp.Paren):
        return f"({_plain(e.this, dialect)})"
    if isinstance(e, exp.And):
        return f"{_plain(e.left, dialect)} and {_plain(e.right, dialect)}"
    if isinstance(e, exp.Or):
        return f"{_plain(e.left, dialect)} or {_plain(e.right, dialect)}"
    if isinstance(e, exp.Not):
        inner = e.this
        if isinstance(inner, exp.In) and inner.expressions:
            values = ", ".join(sql(v) for v in inner.expressions)
            return f"{sql(inner.this)} is not one of {values}"
        if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
            return f"{sql(inner.this)} is not empty"
        if isinstance(inner, exp.Like):
            return f"{sql(inner.this)} does not match the pattern {sql(inner.expression)}"
        return f"not ({_plain(inner, dialect)})"
    for kind, word in _WORDS.items():
        if type(e) is kind:
            return f"{sql(e.left)} {word} {sql(e.right)}"
    if isinstance(e, exp.In):
        if e.expressions:
            return f"{sql(e.this)} is one of {', '.join(sql(v) for v in e.expressions)}"
        return f"{sql(e.this)} is in the result of a subquery"
    if isinstance(e, exp.Is) and isinstance(e.expression, exp.Null):
        return f"{sql(e.this)} is empty"
    if isinstance(e, exp.Between):
        return f"{sql(e.this)} is between {sql(e.args['low'])} and {sql(e.args['high'])}"
    if isinstance(e, exp.Like):
        return f"{sql(e.this)} matches the pattern {sql(e.expression)}"
    return sql(e)


def _as_date(e: exp.Expression) -> date | None:
    if isinstance(e, exp.Cast):
        e = e.this
    if isinstance(e, exp.Literal) and e.is_string:
        try:
            return datetime.fromisoformat(e.this.strip()).date()
        except ValueError:
            return None
    return None


def _date_range(e: exp.Expression) -> tuple[str, date | None, date | None] | None:
    """For `column <op> date` (or BETWEEN), the column and the days it keeps."""
    if isinstance(e, exp.Between) and isinstance(e.this, exp.Column):
        low, high = _as_date(e.args["low"]), _as_date(e.args["high"])
        return (e.this.name, low, high) if low and high else None
    if not isinstance(e, tuple(_WORDS)) or isinstance(e, exp.NEQ):
        return None
    column, value, kind = e.left, e.right, type(e)
    if isinstance(value, exp.Column) and not isinstance(column, exp.Column):
        flipped = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE}
        column, value, kind = value, column, flipped.get(kind, kind)
    day = _as_date(value)
    if not isinstance(column, exp.Column) or day is None:
        return None
    one = timedelta(days=1)
    earliest, latest = {
        exp.EQ: (day, day),
        exp.GT: (day + one, None),
        exp.GTE: (day, None),
        exp.LT: (None, day - one),
        exp.LTE: (None, day),
    }[kind]
    return column.name, earliest, latest


def describe_filters(statement: exp.Expression, dialect: str | None = None) -> list[QueryFilter]:
    """Every way a validated query leaves rows out, in the order they appear."""
    found: list[QueryFilter] = []
    for node in statement.walk():
        if isinstance(node, exp.Select):
            if node.args.get("distinct"):
                found.append(
                    QueryFilter(
                        clause="DISTINCT", sql="SELECT DISTINCT", plain="removes duplicate rows"
                    )
                )
            for join in node.args.get("joins") or []:
                on = join.args.get("on")
                target = join.this.sql(dialect=dialect)
                plain = f"joins {target}" + (f" on {_plain(on, dialect)}" if on else "")
                found.append(QueryFilter(clause="JOIN", sql=join.sql(dialect=dialect), plain=plain))
            for clause in ("where", "having"):
                condition = node.args.get(clause)
                if condition is None:
                    continue
                for part in _conjuncts(condition.this):
                    bounds = _date_range(part) if clause == "where" and node is statement else None
                    found.append(
                        QueryFilter(
                            clause=clause.upper(),
                            sql=part.sql(dialect=dialect),
                            plain=_plain(part, dialect),
                            **(
                                dict(
                                    zip(("date_column", "earliest", "latest"), bounds, strict=True)
                                )
                                if bounds
                                else {}
                            ),
                        )
                    )
        elif isinstance(node, exp.Union) and node.args.get("distinct"):
            found.append(
                QueryFilter(
                    clause="UNION", sql="UNION", plain="combines results and removes duplicate rows"
                )
            )
        elif isinstance(node, exp.Except | exp.Intersect):
            label = _label(node)
            other = node.expression.sql(dialect=dialect)
            plain = (
                "keeps only rows that are not in a second result"
                if label == "EXCEPT"
                else "keeps only rows that are also in a second result"
            )
            found.append(QueryFilter(clause=label, sql=f"{label} {other}", plain=plain))
    return found


class SQLConnector(Connector):
    connection_type = ConnectionType.SQL

    def fetch(self, query: str | None = None) -> ExtractData:
        if query is None:
            raise ConnectorError("SQL connections need a query")
        url = make_url(self.connection.config["url"])
        backend = url.get_backend_name()
        dialect = _DIALECTS.get(backend)
        statement = validate_select(query, dialect)

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
        data = build_extract(self.connection.id, query, frame)
        filters = describe_filters(statement, dialect)
        extract = data.extract.model_copy(update={"query_filters": filters})
        return ExtractData(extract=extract, frame=data.frame)
