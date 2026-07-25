from __future__ import annotations

import json
from pathlib import Path

import httpx
import jsonschema
import typer
from rich.console import Console
from rich.markup import escape

from devtemplate.cli_support import unwrap_or_exit
from devtemplate.config import load_settings
from devtemplate.merge import merge_layer
from devtemplate.schema import validate_devcontainer_config
from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    sync_templates,
)

app = typer.Typer(
    help="Scaffold and evolve a project's devcontainer.json from templates."
)
console = Console()

IDENTITY_FIELDS = {"name", "workspaceFolder", "workspaceMount"}


@app.command("init")
def init(
    path: Path = typer.Argument(  # noqa: B008
        ..., help="Project directory to scaffold."
    ),
    template: str = typer.Option(  # noqa: B008
        ..., help="Cached template name to scaffold from."
    ),
    refresh: bool = typer.Option(  # noqa: B008
        False, help="Sync templates from GitHub before scaffolding."
    ),
) -> None:
    settings = unwrap_or_exit(load_settings(), console)

    if refresh or not list_cached_templates(settings):
        with httpx.Client() as client:
            sync_result = sync_templates(settings, client)
        unwrap_or_exit(sync_result, console, prefix="Sync failed: ")

    config = unwrap_or_exit(load_cached_template(settings, template), console)

    config["name"] = path.resolve().name

    try:
        validate_devcontainer_config(config)
    except jsonschema.ValidationError as exc:
        console.print(
            f"[red]Template '{escape(template)}' is not a valid devcontainer.json:[/red] "
            f"{escape(exc.message)}"
        )
        raise typer.Exit(code=1) from exc

    devcontainer_dir = path / ".devcontainer"
    target = devcontainer_dir / "devcontainer.json"
    if target.exists():
        console.print(
            f"[red]{escape(str(target))} already exists.[/red] "
            "Use 'dvt project add-feature' to layer onto it instead."
        )
        raise typer.Exit(code=1)
    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")
    console.print(f"Scaffolded {target} from template '{template}'.")


@app.command("add-feature")
def add_feature(name: str) -> None:
    settings = unwrap_or_exit(load_settings(), console)

    target = Path(".devcontainer") / "devcontainer.json"
    if not target.exists():
        console.print(
            f"[red]{escape(str(target))} not found.[/red] Run 'dvt project init' first."
        )
        raise typer.Exit(code=1)

    try:
        base_config = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        console.print(
            f"[red]{escape(str(target))} is not strict JSON "
            "(comments/trailing commas are not supported).[/red] "
            "Add this feature's devcontainer.json snippet by hand instead."
        )
        raise typer.Exit(code=1) from exc

    template = unwrap_or_exit(load_cached_template(settings, name), console)

    overlay = {
        key: value for key, value in template.items() if key not in IDENTITY_FIELDS
    }
    merged = merge_layer(base_config, overlay)

    try:
        validate_devcontainer_config(merged)
    except jsonschema.ValidationError as exc:
        console.print(
            f"[red]Merging '{escape(name)}' would produce an invalid devcontainer.json:[/red] "
            f"{escape(exc.message)}"
        )
        raise typer.Exit(code=1) from exc

    target.write_text(json.dumps(merged, indent=2) + "\n")
    console.print(f"Merged feature '{name}' into {target}.")
