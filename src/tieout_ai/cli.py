"""Command-line interface. Human-only actions live here.

Confirming an assumption and refreshing a registered file are things only a
person may do. The MCP server never exposes them, and both run only in an
interactive terminal and ask the person to retype a random code, so they cannot
be driven by piped input, a script, or an AI agent's shell tool.
"""

from __future__ import annotations

import getpass
import secrets
import shutil
import sys
import tempfile
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from tieout_ai import __version__
from tieout_ai.assumptions import AssumptionError
from tieout_ai.certify import confirmation_status
from tieout_ai.connections import (
    ConnectionStoreError,
    connection_problem,
    file_registration,
    sign_connection,
)
from tieout_ai.connectors.sql import sqlite_file
from tieout_ai.models import CertificateStatus, Connection, ConnectionType, source_names
from tieout_ai.project import init_folder
from tieout_ai.render import money, render_html
from tieout_ai.session import CertificateError, Workspace
from tieout_ai.signing import (
    MIN_PASSPHRASE,
    Signer,
    SigningError,
    init_keys,
    keys_dir,
    load_signer,
    load_trusted_key,
)

app = typer.Typer(help="AI TieOut command-line interface.", no_args_is_help=True)
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
    """True only when a person can type: stdin is a real terminal or console.

    On Windows isatty() is also true for the NUL device, so a console is
    confirmed by asking Windows for the handle's console mode.
    """
    if not sys.stdin.isatty():
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt

    mode = ctypes.c_uint32()
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


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
    """tieout_ai command-line interface."""
    ctx.obj = {"workspace": Workspace(home)}


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)


@app.command()
def demo(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Folder for the demo (default: a new temporary folder)."),
    ] = None,
    open_html: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the HTML certificates.")
    ] = True,
) -> None:
    """Run the clean and messy fixtures end to end, with a throwaway key, and show them."""
    from tieout_ai.demo import run_demo

    folder = (out or Path(tempfile.mkdtemp(prefix="recon-demo-"))).resolve()
    typer.echo(f"Running the demo in {folder}")
    typer.echo("It makes a throwaway confirmer key there; your own key is not used.")
    typer.echo("")
    for result in run_demo(folder):
        status = result.status + (", signed off" if result.signed_off else "")
        typer.echo(f"  {result.case:<6} {result.certificate_id}  {status}")
        typer.echo(f"         gap {money(result.gap)}, unexplained (net) {money(result.residual)}")
        typer.echo(f"         {result.html}")
        if open_html:
            webbrowser.open(result.html.as_uri())
    typer.echo("")
    typer.echo("The clean pair reconciles. The messy pair stays OPEN: what no check can")
    typer.echo("prove is reported as unexplained, not forced to zero.")


@app.command()
def init(
    folder: Annotated[
        Path, typer.Argument(help="Folder to set up (default: the current folder).")
    ] = Path("."),
) -> None:
    """Set a folder up for reconciling with Claude Code. Safe to run again."""
    folder = folder.resolve()
    typer.echo(f"Setting up {folder}")
    for path, status in init_folder(folder):
        typer.echo(f"  {path:<24} {status}")

    missing = [name for name in ("recon", "tieout-mcp") if shutil.which(name) is None]
    if missing:
        typer.echo("")
        typer.echo(
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not on your PATH,"
        )
        typer.echo("so Claude Code cannot start the server. Fix it with:")
        typer.echo("    uv tool update-shell")
        typer.echo("then open a new terminal (and restart Claude Code).")

    typer.echo("")
    key = load_trusted_key()
    if key is None and _stdin_is_tty():
        typer.echo("No confirmer key yet. Confirmations must be signed, so create one now.")
        name = typer.prompt("Your name, as confirmations will show it", default=getpass.getuser())
        passphrase = typer.prompt(
            f"Choose a passphrase (at least {MIN_PASSPHRASE} characters)",
            hide_input=True,
            confirmation_prompt=True,
        )
        try:
            key = init_keys(name, passphrase)
        except SigningError as error:
            _fail(str(error))
            return
    if key is None:
        typer.echo("No confirmer key yet. In your own terminal, run:")
        typer.echo('    tieout keys init --name "Your Name"')
    else:
        typer.echo(f"Confirmer key: {key.owner} ({key.fingerprint})")

    typer.echo("")
    typer.echo("Next steps (you, not the AI):")
    typer.echo("  1. Add your data sources here, for example:")
    typer.echo("       tieout connections add-file dashboard C:\\data\\dashboard.csv")
    typer.echo("       tieout connections add-sql ledger")
    typer.echo(f"  2. Open Claude Code in {folder} and approve the recon server.")
    typer.echo("  3. Ask it to reconcile. Confirm its assumptions yourself, in your own terminal:")
    typer.echo("       tieout confirm <assumption_id>")


@app.command()
def confirm(
    ctx: typer.Context,
    assumption_id: Annotated[str, typer.Argument(help="ID of the assumption to confirm.")],
) -> None:
    """Confirm an assumption, signed with your key. Interactive only."""
    _require_terminal("tieout confirm")
    store = _workspace(ctx).assumptions
    try:
        assumption = store.get(assumption_id)
    except AssumptionError as error:
        _fail(str(error))
        return
    trusted = load_trusted_key()
    if trusted is None:
        _fail("No confirmer key on this machine. Create one first with `tieout keys init`.")
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
    _require_terminal("tieout keys init")
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


@keys_app.command("fingerprint")
def key_fingerprint() -> None:
    """Print this machine's confirmer key fingerprint, to compare with a certificate's."""
    trusted = load_trusted_key()
    if trusted is None:
        _fail(f"No confirmer key in {keys_dir()}. Create one with `tieout keys init`.")
        return
    typer.echo(trusted.fingerprint)


@keys_app.command("show")
def show_key() -> None:
    """Show whose confirmer key this machine uses, and its fingerprint."""
    trusted = load_trusted_key()
    if trusted is None:
        typer.echo(f"No confirmer key in {keys_dir()}. Create one with `tieout keys init`.")
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


@cert_app.command("sign")
def sign_certificate(
    ctx: typer.Context,
    cert_id: Annotated[str, typer.Argument(help="Certificate ID.")],
) -> None:
    """Sign a RECONCILED certificate off, making it final. Interactive only."""
    _require_terminal("tieout cert sign")
    workspace = _workspace(ctx)
    try:
        cert = workspace.load_certificate(cert_id)
    except CertificateError as error:
        _fail(str(error))
        return
    if cert.status != CertificateStatus.RECONCILED:
        _fail(f"Only a RECONCILED certificate can be signed off; this one is {cert.status.value}")
        return
    trusted = load_trusted_key()
    warning = confirmation_status(cert, trusted)
    if warning:
        _fail(f"Its confirmations cannot be verified on this machine: {warning}")
        return
    if workspace.sign_off(cert).signed:
        typer.echo(f"{cert.id} is already signed off.")
        return

    name_a, name_b = source_names(cert.extracts[0].connection_id, cert.extracts[1].connection_id)
    typer.echo(f"{cert.id}  RECONCILED")
    typer.echo(f"  {name_a} vs {name_b}, gap {money(cert.match.gap)}, fully explained")
    typer.echo(f"  {len(cert.assumptions)} assumptions, all confirmed with key")
    typer.echo(f"  {cert.confirmer_key_fingerprint}")
    typer.echo("Signing off makes this certificate final.")
    if not _person_retypes_code("sign it off"):
        _fail("The code did not match; nothing was signed.")
        return
    signer = _unlock_key("sign it off")
    if signer is None:
        return
    workspace.save_sign_off(cert, signer)
    sign_off = workspace.sign_off(cert)
    html = workspace.certificate_dir / f"{cert.id}.html"
    html.write_text(render_html(cert, sign_off), encoding="utf-8")
    typer.echo(f"Signed off {cert.id} by {sign_off.by} (key {sign_off.fingerprint}).")
    typer.echo(f"  Updated {html}")


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
    sign_off = workspace.sign_off(cert)
    out(f"{cert.id}  {cert.status.value}")
    if cert.confirmer_key_fingerprint:
        out(f"Expected confirmer key: {cert.confirmer_key_fingerprint} ({cert.confirmer})")
        out("  Check it matches `tieout keys fingerprint` on the confirmer's own machine.")
    else:
        out("No confirmer key is recorded: its confirmations cannot be verified.")
    out(f"Hash verified: {cert.certificate_hash}")
    out(f"Issued {cert.created_at.isoformat()} by engine {cert.engine_version}")
    if cert.status == CertificateStatus.RECONCILED:
        if sign_off.signed:
            out(f"SIGNED OFF by {sign_off.by} at {sign_off.at} (key {sign_off.fingerprint}): final")
        elif sign_off.problem:
            out(f"NOT SIGNED OFF: {sign_off.problem}")
        else:
            out(f"NOT SIGNED OFF: not final until a person runs `tieout cert sign {cert.id}`")
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
    warning = confirmation_status(cert, load_trusted_key())
    if warning:
        out(f"CONFIRMATIONS: {warning}")
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
        html.write_text(render_html(cert, sign_off), encoding="utf-8")
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
    """Register a CSV or Excel file, signed with your key. Interactive only."""
    _require_terminal("tieout connections add-file")
    resolved = path.resolve()
    if not resolved.is_file():
        _fail(f"No file at {resolved}")
        return
    signer = _unlock_key("register it")
    if signer is None:
        return
    registered = {
        **file_registration(resolved),
        "registered_by": signer.owner,
        "registered_at": _now(),
    }
    conn = Connection(
        id=connection_id,
        type=ConnectionType.FILE,
        config={"path": str(resolved), "registered": registered},
    )
    if not _store_signed(ctx, conn, signer, replace):
        return
    typer.echo(f"Added file connection '{connection_id}' -> {resolved}, signed by {signer.owner}")
    typer.echo(f"  Registered sha256 {registered['sha256'][:16]}... ({registered['size']:,} bytes)")
    typer.echo("  If the file changes, certificates stay OPEN until a person runs")
    typer.echo(f"  `tieout connections refresh {connection_id}`.")


@connections_app.command("refresh")
def refresh_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Connection to re-register.")],
) -> None:
    """Re-register a source you have checked, signed with your key. Interactive only."""
    _require_terminal("tieout connections refresh")
    store = _workspace(ctx).connections
    try:
        conn = store.get(connection_id)
    except ConnectionStoreError as error:
        _fail(str(error))
        return
    source = _registered_file(conn)
    if source is None:
        _fail(
            f"Connection '{connection_id}' has no file to re-register; "
            "re-add it with `--replace` instead"
        )
        return
    if not source.is_file():
        _fail(f"No file at {source}")
        return
    previous = conn.config.get("registered") or {}
    current = file_registration(source, sqlite=conn.type == ConnectionType.SQL)
    typer.echo(f"Connection '{connection_id}' -> {source}")
    typer.echo(f"  registered sha256 {str(previous.get('sha256', 'none'))[:16]}...")
    typer.echo(f"  file now   sha256 {current['sha256'][:16]}... ({current['size']:,} bytes)")
    typer.echo("Refresh only if you have checked the file is the right one.")
    if not _person_retypes_code("re-register it"):
        _fail("The code did not match; nothing was changed.")
        return
    signer = _unlock_key("re-register it")
    if signer is None:
        return
    registered = {
        **current,
        "registered_by": previous.get("registered_by"),
        "registered_at": previous.get("registered_at"),
        "refreshed_by": signer.owner,
        "refreshed_at": _now(),
    }
    updated = conn.model_copy(update={"config": {**conn.config, "registered": registered}})
    if _store_signed(ctx, updated, signer, replace=True):
        typer.echo(f"Refreshed '{connection_id}', signed by {signer.owner}.")


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
    """Register a SQL database, signed with your key. Use a read-only database user."""
    from sqlalchemy.engine import make_url

    _require_terminal("tieout connections add-sql")
    if url is None:
        url = typer.prompt("Database URL", hide_input=True)
    try:
        parsed = make_url(url)
    except Exception:
        _fail("That is not a valid SQLAlchemy URL")
        return
    signer = _unlock_key("register it")
    if signer is None:
        return
    registered: dict = {"registered_by": signer.owner, "registered_at": _now()}
    database = sqlite_file(url)
    if database is not None and database.is_file():
        registered.update(file_registration(database, sqlite=True))
    conn = Connection(
        id=connection_id,
        type=ConnectionType.SQL,
        config={"url": url, "registered": registered},
    )
    if not _store_signed(ctx, conn, signer, replace):
        return
    typer.echo(
        f"Added SQL connection '{connection_id}' -> {parsed.render_as_string(hide_password=True)}"
        f", signed by {signer.owner}"
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
    key = load_trusted_key()
    for conn in connections:
        if conn.type == ConnectionType.SQL:
            where = make_url(conn.config["url"]).render_as_string(hide_password=True)
        else:
            where = conn.config.get("path", "")
        registered = conn.config.get("registered") or {}
        if registered.get("sha256"):
            where += f"  (sha256 {registered['sha256'][:12]}...)"
        problem = connection_problem(conn, key)
        signed = (
            f"NOT SIGNED: {problem}" if problem else f"signed by {conn.config['signature']['by']}"
        )
        typer.echo(f"{conn.id}  [{conn.type.value}]  {where}  [{signed}]")


def _unlock_key(action: str) -> Signer | None:
    trusted = load_trusted_key()
    if trusted is None:
        _fail("No confirmer key on this machine. Create one first with `tieout keys init`.")
        return None
    passphrase = typer.prompt(f"Passphrase for {trusted.owner}'s key, to {action}", hide_input=True)
    try:
        return load_signer(passphrase)
    except SigningError as error:
        _fail(f"{error}; nothing was changed.")
        return None


def _store_signed(ctx: typer.Context, conn: Connection, signer: Signer, replace: bool) -> bool:
    try:
        _workspace(ctx).connections.add(sign_connection(conn, signer), replace=replace)
    except ConnectionStoreError as error:
        _fail(str(error))
        return False
    return True


def _registered_file(conn: Connection) -> Path | None:
    """The file a connection's registration hashes, if any."""
    if conn.type == ConnectionType.FILE:
        return Path(conn.config["path"])
    if conn.type == ConnectionType.SQL:
        return sqlite_file(conn.config["url"])
    return None


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


if __name__ == "__main__":
    app()
