from pathlib import Path

import typer

from drx.storage.database import create_database

app = typer.Typer(help="DevRelay-X command line")


@app.command()
def init() -> None:
    """Initialize the local SQLite database."""
    create_database()
    typer.echo("Initialized DevRelay-X at .drx/devrelay.sqlite")


@app.command()
def status() -> None:
    """Show the current workspace foundation status."""
    database = Path(".drx/devrelay.sqlite")
    state = "initialized" if database.exists() else "not initialized"
    typer.echo(f"DevRelay-X v0.1: {state}")


if __name__ == "__main__":
    app()
