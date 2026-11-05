"""Command-line interface. Human-only actions (confirming assumptions) live here.

The MCP server never exposes `confirm`. On top of that, `recon confirm` only runs
in an interactive terminal and asks the person to retype a random code, so it
cannot be driven by piped input, a script, or an AI agent's shell tool.
"""

from __future__ import annotations

import getpass
import secrets
import sys
from pathlib import Path
from typing import Annotated

import typer

from recon_engine import __version__
from recon_engine.assumptions import AssumptionError
from recon_engine.connections import ConnectionStoreError
from recon_engine.models import CertificateStatus, Connection, ConnectionType
from recon_engine.render import money, render_html
from recon_engine.session import CertificateError, Workspace

app = typer.Typer(help="recon_engine command-line interface.", no_args_is_help=True)
assumptions_app = typer.Typer(help="Inspect assumptions.", no_args_is_help=True)
cert_app = typer.Typer(help="View and check certificates.", no_args_is_help=True)
app.add_typer(assumptions_app, name="assumptions")
connections_app = typer.Typer(
    help="Manage data sources. Credentials stay on this machine.", no_args_is_help=True
)
app.add_typer(cert_app, name="cert")
app.add_typer(connections_app, name="connections")


def _workspace(ctx: typer.Context) -> Workspace:
    return ctx.obj["workspace"]


# No 0/O, 1/I/L: easy to read back and retype.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def _confirmation_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


@app.callback()
def main(
    ctx: typer.Context,
    home: Annotated[
        Path | None,
        typer.Option("--home", envvar="RECON_HOME", help="Workspace directory (default: .recon)."),
    ] = None,
) -> None:
    """recon_engine command-line interface."""
    ctx.obj = {"workspace": Workspace(home)}


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)


@app.command()
def confirm(
    ctx: typer.Context,
    assumption_id: Annotated[str, typer.Argument(help="ID of the assumption to confirm.")],
    by: Annotated[
        str | None,
        typer.Option("--by", help="Name of the person confirming (default: your login name)."),
    ] = None,
) -> None:
    """Confirm an assumption. Interactive only: you retype a code to prove you are here."""
    if not _stdin_is_tty():
        _fail(
            "recon confirm must be run by a person in an interactive terminal; "
            "it does not accept piped or scripted input."
        )
        return
    store = _workspace(ctx).assumptions
    try:
        assumption = store.get(assumption_id)
    except AssumptionError as error:
        _fail(str(error))
        return
    if assumption.confirmed:
        typer.echo(f"Already confirmed by {assumption.confirmed_by}: {assumption.id}")
        return

    typer.echo(f"Assumption {assumption.id} (proposed by {assumption.proposed_by}):")
    typer.echo(f"  {assumption.description}")
    typer.echo(f"  {assumption.parameter} = {assumption.value}")
    code = _confirmation_code()
    typed = typer.prompt(
        f"Type {code} to confirm (anything else cancels)", default="", show_default=False
    )
    if typed.strip().upper() != code:
        _fail("The code did not match; nothing was confirmed.")
        return
    confirmed = store.confirm(assumption_id, by or getpass.getuser())
    typer.echo(f"Confirmed {confirmed.id} by {confirmed.confirmed_by}")
    typer.echo(f"  {confirmed.description}")
    typer.echo("Re-issue any certificate that relies on it to record the confirmation.")


@assumptions_app.command("list")
def list_assumptions(ctx: typer.Context) -> None:
    """List every assumption in the workspace."""
    assumptions = _workspace(ctx).assumptions.all()
    if not assumptions:
        typer.echo("No assumptions yet.")
        return
    for a in assumptions:
        state = f"confirmed by {a.confirmed_by}" if a.confirmed else "unconfirmed"
        typer.echo(f"{a.id}  [{state}]  proposed by {a.proposed_by}")
        typer.echo(f"    {a.description}")


@cert_app.command("list")
def list_certificates(ctx: typer.Context) -> None:
    """List issued certificates."""
    ids = _workspace(ctx).list_certificates()
    typer.echo("\n".join(ids) if ids else "No certificates yet.")


@cert_app.command("show")
def show_certificate(
    ctx: typer.Context,
    cert_id: Annotated[str, typer.Argument(help="Certificate ID.")],
    html: Annotated[
        Path | None, typer.Option("--html", help="Also write the HTML report here.")
    ] = None,
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Also write the JSON here.")
    ] = None,
) -> None:
    """Show a certificate, verify its hash, and optionally export it."""
    workspace = _workspace(ctx)
    try:
        cert = workspace.load_certificate(cert_id)
    except CertificateError as error:
        _fail(str(error))
        return

    out = typer.echo
    out(f"{cert.id}  {cert.status.value}")
    out(f"Hash verified: {cert.certificate_hash}")
    out(f"Issued {cert.created_at.isoformat()} by engine {cert.engine_version}")
    out("")
    out(f"  Side A total   {money(cert.match.total_a):>16}")
    out(f"  Side B total   {money(cert.match.total_b):>16}")
    out(f"  Gap            {money(cert.match.gap):>16}")
    for bucket in cert.buckets:
        label = f"{bucket.check_name} ({bucket.row_count} rows)"
        out(f"    {label:<30} {money(bucket.amount):>16}")
    out(f"  Unexplained    {money(cert.residual):>16}  ({len(cert.residual_items)} items)")
    quality = sum(1 for i in cert.residual_items if i.kind == "data_quality")
    if quality:
        out(f"    of which {quality} are data-quality items (e.g. empty amounts)")
    if cert.checks_without_findings:
        runs = ", ".join(r.check_name for r in cert.checks_without_findings)
        out(f"  Checks with no findings: {runs}")
    if cert.supersedes:
        out(f"  Supersedes {cert.supersedes}")
    out("")

    if cert.unconfirmed_assumptions:
        out(f"UNCONFIRMED: {len(cert.unconfirmed_assumptions)} assumption(s) were not confirmed")
        out("by a person when this certificate was issued:")
    else:
        out("All assumptions were confirmed when this certificate was issued.")
    current = workspace.assumptions
    for a in cert.assumptions:
        mark = "confirmed" if a.confirmed else "UNCONFIRMED"
        out(f"  {a.id} [{mark}] {a.description}")
        if not a.confirmed and a.id in current and current.get(a.id).confirmed:
            out(
                f"    (confirmed since this certificate was issued, by "
                f"{current.get(a.id).confirmed_by}; re-issue to record it)"
            )

    if cert.status == CertificateStatus.OPEN:
        out("")
        out("Status is OPEN: some differences are not explained.")
    elif cert.status == CertificateStatus.PROVISIONAL:
        out("")
        out("Status is PROVISIONAL: everything is explained, but it rests on unconfirmed")
        out("assumptions. It becomes RECONCILED only when re-issued after a person confirms them.")

    if html is not None:
        html.write_text(render_html(cert), encoding="utf-8")
        out(f"Wrote HTML to {html}")
    if json_out is not None:
        json_out.write_text(cert.model_dump_json(indent=2) + "\n", encoding="utf-8")
        out(f"Wrote JSON to {json_out}")


@connections_app.command("add-file")
def add_file_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Short ID, e.g. dashboard.")],
    path: Annotated[Path, typer.Argument(help="CSV or Excel file.")],
    replace: Annotated[bool, typer.Option("--replace", help="Overwrite an existing ID.")] = False,
) -> None:
    """Add a CSV or Excel file as a data source."""
    resolved = path.resolve()
    if not resolved.is_file():
        _fail(f"No file at {resolved}")
        return
    conn = Connection(id=connection_id, type=ConnectionType.FILE, config={"path": str(resolved)})
    try:
        _workspace(ctx).connections.add(conn, replace=replace)
    except ConnectionStoreError as error:
        _fail(str(error))
        return
    typer.echo(f"Added file connection '{connection_id}' -> {resolved}")


@connections_app.command("add-sql")
def add_sql_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Short ID, e.g. ledger.")],
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help="SQLAlchemy URL. Omit to type it at a hidden prompt (keeps it out of "
            "shell history).",
        ),
    ] = None,
    replace: Annotated[bool, typer.Option("--replace", help="Overwrite an existing ID.")] = False,
) -> None:
    """Add a SQL database. Use a read-only database user."""
    from sqlalchemy.engine import make_url

    if url is None:
        url = typer.prompt("Database URL", hide_input=True)
    try:
        parsed = make_url(url)
    except Exception:
        _fail("That is not a valid SQLAlchemy URL")
        return
    conn = Connection(id=connection_id, type=ConnectionType.SQL, config={"url": url})
    try:
        _workspace(ctx).connections.add(conn, replace=replace)
    except ConnectionStoreError as error:
        _fail(str(error))
        return
    typer.echo(
        f"Added SQL connection '{connection_id}' -> {parsed.render_as_string(hide_password=True)}"
    )
    typer.echo("Use a read-only database user: the engine only runs SELECTs, but defence in depth.")


@connections_app.command("list")
def list_connections(ctx: typer.Context) -> None:
    """List connections. Passwords are never shown."""
    from sqlalchemy.engine import make_url

    connections = _workspace(ctx).connections.all()
    if not connections:
        typer.echo("No connections yet.")
        return
    for conn in connections:
        if conn.type == ConnectionType.SQL:
            where = make_url(conn.config["url"]).render_as_string(hide_password=True)
        else:
            where = conn.config.get("path", "")
        typer.echo(f"{conn.id}  [{conn.type.value}]  {where}")


@connections_app.command("remove")
def remove_connection(
    ctx: typer.Context, connection_id: Annotated[str, typer.Argument(help="Connection ID.")]
) -> None:
    """Remove a connection."""
    try:
        _workspace(ctx).connections.remove(connection_id)
    except ConnectionStoreError as error:
        _fail(str(error))
        return
    typer.echo(f"Removed connection '{connection_id}'")
