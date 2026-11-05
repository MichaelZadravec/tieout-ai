"""MCP server: lets an AI agent drive a reconciliation by reference and parameter.

Run with `recon-mcp` (stdio). The AI proposes; the engine proves. Tools take
IDs and parameters only, return engine-computed numbers, and there is no tool
to confirm an assumption: only a person can, with `recon confirm` in a terminal.
"""

from __future__ import annotations

import argparse
import functools
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from recon_engine.assumptions import AssumptionError
from recon_engine.certify import ConservationError
from recon_engine.checks import CheckError
from recon_engine.connectors import ConnectorError
from recon_engine.match import MatchError
from recon_engine.service import ReconService, ServiceError
from recon_engine.session import CertificateError, Workspace

INSTRUCTIONS = """\
recon_engine proves whether two business numbers agree and explains every dollar
of any difference. You propose; the engine proves.

Workflow: list_connections -> profile_source / run_query for each side -> match
(choose keys, amount columns, dates, period) -> list_open_items and get_rows to
understand the differences -> list_checks, then run_check with a hypothesis ->
repeat until the residual is explained or you cannot explain more -> certify.

Rules:
- Pass only IDs and parameters. Never pass data values as if they were results.
- Every number you report must come from a tool result. Quote certificate IDs.
- A check that finds nothing returns found=false: that hypothesis is wrong.
- Use propose_assumption for choices that affect the result (scope, filters).
- You cannot confirm assumptions. A PROVISIONAL certificate needs a person to run
  `recon confirm <id>` in a terminal. Say so; do not try to work around it.
- Report unexplained differences honestly; do not force a residual to zero.
"""

_ENGINE_ERRORS = (
    AssumptionError,
    CertificateError,
    CheckError,
    ConnectorError,
    ConservationError,
    MatchError,
    ServiceError,
    ValidationError,
)


def _tool(server: MCPServer) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a tool, turning engine errors into messages the AI can act on."""

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except _ENGINE_ERRORS as error:
                raise ToolError(str(error)) from None

        server.tool()(wrapper)
        return wrapper

    return register


def build_server(workspace: Workspace | None = None) -> MCPServer:
    service = ReconService(workspace or Workspace())
    server = MCPServer("recon_engine", instructions=INSTRUCTIONS)
    tool = _tool(server)

    @tool
    def list_connections() -> list[dict[str, str]]:
        """List the data sources configured on this machine (IDs and types only)."""
        return service.list_connections()

    @tool
    def profile_source(connection_id: str, query: str | None = None) -> dict[str, Any]:
        """Fetch from a connection and profile every column (counts, ranges, common values).

        `query` is a single read-only SELECT for SQL connections, or a sheet name for
        Excel files; omit it for CSV. Also registers the result as an extract.
        """
        return service.profile_source(connection_id, query)

    @tool
    def run_query(connection_id: str, query: str | None = None) -> dict[str, Any]:
        """Fetch an extract. Returns its ID, row count, column totals and hashes (not rows).

        `query` is a single read-only SELECT for SQL connections (anything else is
        refused), a sheet name for Excel files, and omitted for CSV.
        """
        return service.run_query(connection_id, query)

    @tool
    def get_rows(
        extract_id: str, offset: int = 0, limit: int = 50, row_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """Read rows of an extract, a page at a time (limit at most 200), or by row ID."""
        return service.get_rows(extract_id, offset, limit, row_ids)

    @tool
    def match(
        extract_a: str,
        extract_b: str,
        key_columns: list[str],
        amount_column: str,
        key_columns_b: list[str] | None = None,
        amount_column_b: str | None = None,
        date_column: str | None = None,
        date_column_b: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> dict[str, Any]:
        """Match two extracts row by row and compute the gap (total A minus total B).

        Columns named for side A apply to side B unless the `_b` versions are given.
        A period (ISO dates, inclusive) needs date_column. Returns a reconciliation_id.
        """
        return service.match(
            extract_a,
            extract_b,
            key_columns,
            amount_column,
            key_columns_b,
            amount_column_b,
            date_column,
            date_column_b,
            period_start,
            period_end,
        )

    @tool
    def list_open_items(
        reconciliation_id: str, kind: str | None = None, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        """List differences not yet explained: only_a, only_b, difference or data_quality.

        Each item's `amount` is its contribution to the gap.
        """
        return service.list_open_items(reconciliation_id, kind, offset, limit)

    @tool
    def list_checks() -> list[dict[str, Any]]:
        """List the discrepancy checks with their parameter schemas and limits."""
        return service.list_checks()

    @tool
    def run_check(reconciliation_id: str, check: str, params: dict[str, Any]) -> dict[str, Any]:
        """Test one hypothesis. The engine claims only the items it can prove.

        found=false means the hypothesis is wrong. Rejected parameters return an error.
        """
        return service.run_check(reconciliation_id, check, params)

    @tool
    def propose_assumption(
        reconciliation_id: str, parameter: str, value: Any, description: str
    ) -> dict[str, Any]:
        """Record an assumption about the whole reconciliation (scope, filters, sources).

        It stays unconfirmed until a person confirms it; you cannot.
        """
        return service.propose_assumption(reconciliation_id, parameter, value, description)

    @tool
    def certify(reconciliation_id: str, supersedes: str | None = None) -> dict[str, Any]:
        """Check conservation and issue a certificate: RECONCILED, PROVISIONAL or OPEN.

        Re-certifying the same reconciliation supersedes its previous certificate.
        """
        return service.certify(reconciliation_id, supersedes)

    @tool
    def get_certificate(certificate_id: str) -> dict[str, Any]:
        """Load an issued certificate, verify its hash, and summarise it."""
        return service.get_certificate(certificate_id)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="recon_engine MCP server (stdio)")
    parser.add_argument(
        "--home", type=Path, default=None, help="Workspace directory (default: .recon)"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    build_server(Workspace(args.home)).run("stdio")


if __name__ == "__main__":
    main()
