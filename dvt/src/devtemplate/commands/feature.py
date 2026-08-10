from __future__ import annotations

import json
from typing import Any

import httpx
import typer
from logerr import Err, Ok
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.cli_support import unwrap_or_exit
from devtemplate.config import load_settings
from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    sync_templates,
)

app = typer.Typer(
    help="Add, remove, and inspect the devcontainer features dvt knows about."
)
console = Console()


def _feature_ref(template: dict[str, Any]) -> str:
    features = template.get("features", {})
    return next(iter(features), "")


@app.command("list")
def list_features(
    json_output: bool = typer.Option(  # noqa: B008
        False, "--json", help="Print machine-readable JSON instead of a table."
    ),
) -> None:
    """List every feature dvt knows about, with its description."""
    settings = unwrap_or_exit(load_settings(), console)

    names = list_cached_templates(settings)
    if not names and not json_output:
        console.print("No cached features. Run 'dvt feature sync' first.")
        raise typer.Exit(code=0)

    rows: list[dict[str, str]] = []
    for name in names:
        match load_cached_template(settings, name):
            case Ok(template):
                rows.append(
                    {
                        "name": name,
                        "description": template.get("description", ""),
                        "image": template.get("image", ""),
                        "feature_ref": _feature_ref(template),
                    }
                )
            case Err(error):
                console.print(
                    f"[red]Skipping {escape(repr(name))}: {escape(str(error))}[/red]"
                )

    if json_output:
        console.print_json(json.dumps(rows))
        return

    table = Table("Name", "Description", "Base Image")
    for row in rows:
        table.add_row(row["name"], row["description"], row["image"])
    console.print(table)


@app.command("show")
def show_feature(
    name: str = typer.Argument(..., help="Cached feature name to show."),  # noqa: B008
) -> None:
    """Print a cached feature's devcontainer.json overlay."""
    settings = unwrap_or_exit(load_settings(), console)

    template = unwrap_or_exit(load_cached_template(settings, name), console)
    console.print_json(json.dumps(template))


@app.command("sync")
def sync() -> None:
    """Refresh the cached feature registry from GitHub."""
    settings = unwrap_or_exit(load_settings(), console)

    with httpx.Client() as client:
        result = sync_templates(settings, client)
    names = unwrap_or_exit(result, console, prefix="Sync failed: ")
    console.print(f"Synced {len(names)} features: {', '.join(names)}")
