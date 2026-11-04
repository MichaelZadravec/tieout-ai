"""Command-line interface. Human-only actions (confirming assumptions) live here.

The MCP server never exposes `confirm`, so only a person at this CLI can confirm
an assumption.
"""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Annotated

import typer

from recon_engine import __version__
from recon_engine.assumptions import AssumptionError
from recon_engine.models import CertificateStatus
from recon_engine.render import money, render_html
from recon_engine.session import CertificateError, Workspace

app = typer.Typer(help="recon_engine command-line interface.", no_args_is_help=True)
assumptions_app = typer.Typer(help="Inspect assumptions.", no_args_is_help=True)
cert_app = typer.Typer(help="View and check certificates.", no_args_is_help=True)
app.add_typer(assumptions_app, name="assumptions")
app.add_typer(cert_app, name="cert")


def _workspace(ctx: typer.Context) -> Workspace:
    return ctx.obj["workspace"]


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
    """Confirm an assumption. This is the only way an assumption becomes confirmed."""
    store = _workspace(ctx).assumptions
    try:
        assumption = store.get(assumption_id)
        if assumption.confirmed:
            typer.echo(f"Already confirmed by {assumption.confirmed_by}: {assumption.id}")
            return
        confirmed = store.confirm(assumption_id, by or getpass.getuser())
    except AssumptionError as error:
        _fail(str(error))
        return
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

    if cert.status == CertificateStatus.OPEN and cert.residual_items:
        out("")
        out("Status is OPEN: some differences are not explained.")

    if html is not None:
        html.write_text(render_html(cert), encoding="utf-8")
        out(f"Wrote HTML to {html}")
    if json_out is not None:
        json_out.write_text(cert.model_dump_json(indent=2) + "\n", encoding="utf-8")
        out(f"Wrote JSON to {json_out}")
