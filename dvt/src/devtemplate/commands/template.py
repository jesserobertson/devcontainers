from __future__ import annotations

import json

import httpx
import typer
from logerr import Err, Ok
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.config import load_settings
from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    sync_templates,
)

app = typer.Typer(help="Inspect and refresh cached devcontainer templates.")
console = Console()


@app.command("list")
def list_templates() -> None:
    match load_settings():
        case Err(error):
            console.print(f"[red]{escape(str(error))}[/red]")
            raise typer.Exit(code=1)
        case Ok(settings):
            pass

    names = list_cached_templates(settings)
    if not names:
        console.print("No cached templates. Run 'dvt template sync' first.")
        raise typer.Exit(code=0)
    table = Table("Name", "Image", "Features")
    for name in names:
        match load_cached_template(settings, name):
            case Ok(template):
                table.add_row(
                    name,
                    template.get("image", "?"),
                    ", ".join(template.get("features", {}).keys()),
                )
            case Err(error):
                console.print(
                    f"[red]Skipping {escape(repr(name))}: {escape(str(error))}[/red]"
                )
    console.print(table)


@app.command("show")
def show_template(name: str) -> None:
    match load_settings():
        case Err(error):
            console.print(f"[red]{escape(str(error))}[/red]")
            raise typer.Exit(code=1)
        case Ok(settings):
            pass

    match load_cached_template(settings, name):
        case Ok(template):
            console.print_json(json.dumps(template))
        case Err(error):
            console.print(f"[red]{escape(str(error))}[/red]")
            raise typer.Exit(code=1)


@app.command("sync")
def sync() -> None:
    match load_settings():
        case Err(error):
            console.print(f"[red]{escape(str(error))}[/red]")
            raise typer.Exit(code=1)
        case Ok(settings):
            pass

    with httpx.Client() as client:
        result = sync_templates(settings, client)
    match result:
        case Ok(names):
            console.print(f"Synced {len(names)} templates: {', '.join(names)}")
        case Err(error):
            console.print(f"[red]Sync failed: {escape(str(error))}[/red]")
            raise typer.Exit(code=1)
