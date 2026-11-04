"""Command-line interface. Human-only actions (e.g. confirming assumptions) live here."""

import typer

from recon_engine import __version__

app = typer.Typer(help="recon_engine command-line interface.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """recon_engine command-line interface."""


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)
