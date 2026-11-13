"""Command-line interface. Human-only actions live here.

Confirming an assumption and refreshing a registered file are things only a
person may do. The MCP server never exposes them, and both run only in an
interactive terminal and ask the person to retype a random code, so they cannot
be driven by piped input, a script, or an AI agent's shell tool.
"""

from __future__ import annotations

import getpass
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from recon_engine import __version__
from recon_engine.assumptions import AssumptionError
from recon_engine.connections import ConnectionStoreError, file_registration
from recon_engine.models import CertificateStatus, Connection, ConnectionType, source_names
from recon_engine.render import money, render_html
from recon_engine.session import CertificateError, Workspace
from recon_engine.signing import (
    MIN_PASSPHRASE,
    SigningError,
    init_keys,
    keys_dir,
    load_signer,
    load_trusted_key,
)

app = typer.Typer(help="recon_engine command-line interface.", no_args_is_help=True)
assumptions_app = typer.Typer(help="Inspect assumptions.", no_args_is_help=True)
cert_app = typer.Typer(help="View and check certificates.", no_args_is_help=True)
app.add_typer(assumptions_app, name="assumptions")
connections_app = typer.Typer(
    help="Manage data sources. Credentials stay on this machine.", no_args_is_help=True
)
keys_app = typer.Typer(help="Your confirmer signing key.", no_args_is_help=True)
app.add_typer(cert_app, name="cert")
app.add_typer(keys_app, name="keys")
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


def _require_terminal(command: str) -> None:
    if not _stdin_is_tty():
        _fail(
            f"{command} must be run by a person in an interactive terminal; "
            "it does not accept piped or scripted input."
        )


def _person_retypes_code(action: str) -> bool:
    code = _confirmation_code()
    typed = typer.prompt(
        f"Type {code} to {action} (anything else cancels)", default="", show_default=False
    )
    return typed.strip().upper() == code


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
) -> None:
    """Confirm an assumption, signed with your key. Interactive only."""
    _require_terminal("recon confirm")
    store = _workspace(ctx).assumptions
    try:
        assumption = store.get(assumption_id)
    except AssumptionError as error:
        _fail(str(error))
        return
    trusted = load_trusted_key()
    if trusted is None:
        _fail("No confirmer key on this machine. Create one first with `recon keys init`.")
        return
    if assumption.confirmed and trusted.problem(assumption) is None:
        typer.echo(f"Already confirmed by {assumption.confirmed_by}: {assumption.id}")
        return
    if assumption.confirmed:
        typer.echo(f"Marked confirmed, but {trusted.problem(assumption)}; confirm it again.")

    typer.echo(f"Assumption {assumption.id} (proposed by {assumption.proposed_by}):")
    if assumption.elevated:
        typer.echo(f"  ELEVATED: {assumption.elevated_reason}")
    typer.echo(f"  {assumption.description}")
    typer.echo(f"  {assumption.parameter} = {assumption.value}")
    if not _person_retypes_code("confirm"):
        _fail("The code did not match; nothing was confirmed.")
        return
    passphrase = typer.prompt(f"Passphrase for {trusted.owner}'s key", hide_input=True)
    try:
        signer = load_signer(passphrase)
    except SigningError as error:
        _fail(f"{error}; nothing was confirmed.")
        return
    confirmed = store.confirm(assumption_id, signer)
    typer.echo(f"Confirmed {confirmed.id} by {confirmed.confirmed_by}, signed with key")
    typer.echo(f"  {confirmed.signing_key_fingerprint}")
    typer.echo(f"  {confirmed.description}")
    typer.echo("Re-issue any certificate that relies on it to record the confirmation.")


@keys_app.command("init")
def init_key(
    name: Annotated[str, typer.Option("--name", help="Your name, as confirmations will show it.")],
) -> None:
    """Create your confirmer key, protected by a passphrase. Interactive only."""
    _require_terminal("recon keys init")
    existing = load_trusted_key()
    if existing is not None:
        typer.echo(f"A confirmer key already exists for {existing.owner} ({existing.fingerprint})")
        typer.echo(f"in {keys_dir()}.")
        return
    passphrase = typer.prompt(
        f"Choose a passphrase (at least {MIN_PASSPHRASE} characters)",
        hide_input=True,
        confirmation_prompt=True,
    )
    try:
        trusted = init_keys(name, passphrase)
    except SigningError as error:
        _fail(str(error))
        return
    typer.echo(f"Created a confirmer key for {trusted.owner} in {keys_dir()}")
    typer.echo(f"  Fingerprint {trusted.fingerprint}")
    typer.echo("Certificates record this fingerprint. Keep a note of it: a certificate")
    typer.echo("whose confirmations were checked against a different key is not yours.")


@keys_app.command("show")
def show_key() -> None:
    """Show whose confirmer key this machine uses, and its fingerprint."""
    trusted = load_trusted_key()
    if trusted is None:
        typer.echo(f"No confirmer key in {keys_dir()}. Create one with `recon keys init`.")
        return
    typer.echo(f"{trusted.owner}  {trusted.fingerprint}")
    typer.echo(f"  {keys_dir()}")


@assumptions_app.command("list")
def list_assumptions(ctx: typer.Context) -> None:
    """List every assumption in the workspace."""
    assumptions = _workspace(ctx).assumptions.all()
    if not assumptions:
        typer.echo("No assumptions yet.")
        return
    elevated = [a for a in assumptions if a.elevated]
    others = [a for a in assumptions if not a.elevated]
    trusted = load_trusted_key()

    def show(a) -> None:
        problem = (trusted.problem(a) if trusted else "no confirmer key") if a.confirmed else None
        if not a.confirmed:
            state = "unconfirmed"
        elif problem:
            state = f"UNVERIFIED: marked confirmed, but {problem}"
        else:
            state = f"confirmed by {a.confirmed_by}, signature verified"
        typer.echo(f"{a.id}  [{state}]  proposed by {a.proposed_by}")
        if a.elevated:
            typer.echo(f"    ELEVATED: {a.elevated_reason}")
        typer.echo(f"    {a.description}")

    if elevated:
        typer.echo("ELEVATED (these loosen a check's default; review them first)")
        for a in elevated:
            show(a)
        if others:
            typer.echo("")
            typer.echo("Other assumptions")
    for a in others:
        show(a)


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
    name_a, name_b = source_names(cert.extracts[0].connection_id, cert.extracts[1].connection_id)

    def amount_line(label: str, amount, indent: int = 2, note: str = "") -> None:
        out(f"{' ' * indent}{label:<{34 - indent}} {money(amount):>16}{note}")

    amount_line(f"{name_a} total", cert.match.total_a)
    amount_line(f"{name_b} total", cert.match.total_b)
    amount_line(f"Gap ({name_a} - {name_b})", cert.match.gap)
    for bucket in cert.buckets:
        amount_line(f"{bucket.check_name} ({bucket.row_count} rows)", bucket.amount, indent=4)
    amount_line("Unexplained (net)", cert.residual, note=f"  ({len(cert.residual_items)} items)")
    amount_line("Unexplained (gross)", cert.gross, note="  (before items offset)")
    quality = sum(1 for i in cert.residual_items if i.kind == "data_quality")
    if quality:
        out(f"    of which {quality} are data-quality items (e.g. empty amounts)")
    if cert.checks_without_findings:
        runs = ", ".join(r.check_name for r in cert.checks_without_findings)
        out(f"  Checks with no findings: {runs}")
    if cert.supersedes:
        out(f"  Supersedes {cert.supersedes}")
    out("")
    for warning in cert.source_warnings:
        out(f"SOURCE WARNING: {warning}")
    if cert.source_warnings:
        out("")

    if cert.unconfirmed_assumptions:
        out(f"UNCONFIRMED: {len(cert.unconfirmed_assumptions)} assumption(s) were not confirmed")
        out("by a person (with a verified signature) when this certificate was issued:")
    else:
        out("All assumptions were confirmed when this certificate was issued.")
    if cert.confirmer_key_fingerprint:
        out(
            f"Confirmations checked against {cert.confirmer}'s key {cert.confirmer_key_fingerprint}"
        )
    current = workspace.assumptions
    for a in sorted(cert.assumptions, key=lambda a: not a.elevated):
        if a.id in cert.unverified_confirmations:
            mark = "UNVERIFIED"
        else:
            mark = "UNCONFIRMED" if a.id in cert.unconfirmed_assumptions else "confirmed"
        out(f"  {a.id} [{mark}] {a.description}")
        if a.id in cert.unverified_confirmations:
            out(f"    confirmation could not be verified: {cert.unverified_confirmations[a.id]}")
        if a.elevated:
            out(f"    ELEVATED: {a.elevated_reason}")
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
    by: Annotated[
        str | None,
        typer.Option("--by", help="Name of the person registering it (default: your login)."),
    ] = None,
) -> None:
    """Add a CSV or Excel file as a data source, recording its hash."""
    resolved = path.resolve()
    if not resolved.is_file():
        _fail(f"No file at {resolved}")
        return
    registered = {
        **file_registration(resolved),
        "registered_by": by or getpass.getuser(),
        "registered_at": _now(),
    }
    conn = Connection(
        id=connection_id,
        type=ConnectionType.FILE,
        config={"path": str(resolved), "registered": registered},
    )
    try:
        _workspace(ctx).connections.add(conn, replace=replace)
    except ConnectionStoreError as error:
        _fail(str(error))
        return
    typer.echo(f"Added file connection '{connection_id}' -> {resolved}")
    typer.echo(f"  Registered sha256 {registered['sha256'][:16]}... ({registered['size']:,} bytes)")
    typer.echo("  If the file changes, certificates stay OPEN until a person runs")
    typer.echo(f"  `recon connections refresh {connection_id}`.")


@connections_app.command("refresh")
def refresh_file_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="File connection to re-register.")],
    by: Annotated[
        str | None,
        typer.Option("--by", help="Name of the person refreshing it (default: your login)."),
    ] = None,
) -> None:
    """Re-register a file whose contents you have checked. Interactive only."""
    _require_terminal("recon connections refresh")
    store = _workspace(ctx).connections
    try:
        conn = store.get(connection_id)
    except ConnectionStoreError as error:
        _fail(str(error))
        return
    if conn.type != ConnectionType.FILE:
        _fail(f"Connection '{connection_id}' is not a file connection")
        return
    path = Path(conn.config["path"])
    if not path.is_file():
        _fail(f"No file at {path}")
        return
    previous = conn.config.get("registered") or {}
    current = file_registration(path)
    typer.echo(f"Connection '{connection_id}' -> {path}")
    typer.echo(f"  registered sha256 {str(previous.get('sha256', 'none'))[:16]}...")
    typer.echo(f"  file now   sha256 {current['sha256'][:16]}... ({current['size']:,} bytes)")
    typer.echo("Refresh only if you have checked the file is the right one.")
    if not _person_retypes_code("re-register it"):
        _fail("The code did not match; nothing was changed.")
        return
    registered = {
        **current,
        "registered_by": previous.get("registered_by"),
        "registered_at": previous.get("registered_at"),
        "refreshed_by": by or getpass.getuser(),
        "refreshed_at": _now(),
    }
    store.add(
        conn.model_copy(update={"config": {**conn.config, "registered": registered}}),
        replace=True,
    )
    typer.echo(f"Refreshed '{connection_id}' by {registered['refreshed_by']}.")


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
            registered = conn.config.get("registered")
            where += (
                f"  (sha256 {registered['sha256'][:12]}...)"
                if registered
                else "  (no file hash: run `recon connections refresh`)"
            )
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
