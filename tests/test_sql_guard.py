import pytest

from tieout_ai.connectors.base import UnsafeQueryError
from tieout_ai.connectors.sql import validate_select


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select invoice_id, amount from invoices where status = 'PAID'",
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        "SELECT 1;",
        "SELECT a FROM x UNION ALL SELECT a FROM y",
        "-- a comment\nSELECT 1",
    ],
)
def test_accepts_a_single_read_only_select(sql):
    validate_select(sql)


@pytest.mark.parametrize(
    ("sql", "dialect", "message"),
    [
        ("INSERT INTO t VALUES (1)", None, "Only SELECT queries are allowed; got INSERT"),
        ("UPDATE t SET a = 1", None, "Only SELECT queries are allowed; got UPDATE"),
        ("DELETE FROM t", None, "Only SELECT queries are allowed; got DELETE"),
        ("DROP TABLE t", None, "Only SELECT queries are allowed; got DROP"),
        ("CREATE TABLE t (a INT)", None, "Only SELECT queries are allowed; got CREATE"),
        ("TRUNCATE TABLE t", None, "Only SELECT queries are allowed"),
        ("GRANT SELECT ON t TO u", None, "Only SELECT queries are allowed"),
        ("EXEC sp_who", "tsql", "Only SELECT queries are allowed"),
        ("PRAGMA writable_schema = 1", "sqlite", "Only SELECT queries are allowed"),
        ("ATTACH DATABASE 'x' AS y", "sqlite", "Only SELECT queries are allowed"),
        ("SELECT 1; DROP TABLE t", None, "single SELECT statement is allowed; found 2"),
        ("SELECT * INTO backup FROM t", None, "forbidden INTO"),
        ("SELECT * FROM t FOR UPDATE", None, "forbidden LOCK"),
        (
            "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
            "postgres",
            "forbidden DELETE",
        ),
        ("", None, "Query is empty"),
        ("   ", None, "Query is empty"),
        ("SELEC 1 FROM", None, "could not be parsed"),
    ],
)
def test_rejects_anything_but_a_read_only_select(sql, dialect, message):
    with pytest.raises(UnsafeQueryError, match=message):
        validate_select(sql, dialect)
