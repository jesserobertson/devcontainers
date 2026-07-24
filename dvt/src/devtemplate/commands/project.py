from __future__ import annotations

import json
from pathlib import Path

import httpx
import typer
from rich.console import Console

from devtemplate.config import Settings
from devtemplate.store import list_cached_templates, load_cached_template, sync_templates

app = typer.Typer(help="Scaffold and evolve a project's devcontainer.json from templates.")
console = Console()


@app.command("init")
def init(
    path: Path = typer.Argument(..., help="Project directory to scaffold."),
    template: str = typer.Option(..., help="Cached template name to scaffold from."),
    refresh: bool = typer.Option(False, help="Sync templates from GitHub before scaffolding."),
) -> None:
    settings = Settings()
    if refresh or not list_cached_templates(settings):
        with httpx.Client() as client:
            sync_templates(settings, client)

    config = load_cached_template(settings, template)
    devcontainer_dir = path / ".devcontainer"
    target = devcontainer_dir / "devcontainer.json"
    if target.exists():
        console.print(
            f"[red]{target} already exists.[/red] "
            "Use 'dvt project add-feature' to layer onto it instead."
        )
        raise typer.Exit(code=1)
    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")
    console.print(f"Scaffolded {target} from template '{template}'.")


@app.command("add-feature")
def add_feature() -> None:
    """Add a feature to an existing devcontainer.json (not yet implemented)."""
    raise NotImplementedError("add-feature is not yet implemented")
