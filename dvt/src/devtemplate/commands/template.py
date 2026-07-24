from __future__ import annotations

import json

import httpx
import typer
from rich.console import Console
from rich.table import Table

from devtemplate.config import Settings
from devtemplate.store import list_cached_templates, load_cached_template, sync_templates

app = typer.Typer(help="Inspect and refresh cached devcontainer templates.")
console = Console()


@app.command("list")
def list_templates() -> None:
    settings = Settings()
    names = list_cached_templates(settings)
    if not names:
        console.print("No cached templates. Run 'dvt template sync' first.")
        raise typer.Exit(code=0)
    table = Table("Name", "Image", "Features")
    for name in names:
        template = load_cached_template(settings, name)
        table.add_row(
            name,
            template.get("image", "?"),
            ", ".join(template.get("features", {}).keys()),
        )
    console.print(table)


@app.command("show")
def show_template(name: str) -> None:
    settings = Settings()
    template = load_cached_template(settings, name)
    console.print_json(json.dumps(template))


@app.command("sync")
def sync() -> None:
    settings = Settings()
    with httpx.Client() as client:
        names = sync_templates(settings, client)
    console.print(f"Synced {len(names)} templates: {', '.join(names)}")
