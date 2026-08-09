from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

_MINIMAL_PIXI_TOML = """\
[workspace]
name = "{name}"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.11"
"""


def _scaffold_pixi_toml(path: Path, name: str) -> None:
    """Write a minimal pixi.toml if the project doesn't already manage its
    own dependencies (via pixi.toml or a pyproject.toml with a [tool.pixi]
    table). Every template's postCreateCommand runs 'pixi install', which
    fails outright with no manifest to install from - so a project scaffolded
    from nothing needs at least this much to make `dvt up` work end to end.
    """
    if (path / "pixi.toml").exists() or (path / "pyproject.toml").exists():
        return
    (path / "pixi.toml").write_text(_MINIMAL_PIXI_TOML.format(name=name))


_PIXI_DETACHED_ENVIRONMENTS_STEP = (
    "mkdir -p ~/.config/pixi && "
    "printf 'detached-environments = true\\n' >> ~/.config/pixi/config.toml"
)


def _prepend_pixi_detached_environments(config: dict[str, Any]) -> None:
    """Prepend a step turning on pixi's 'detached-environments' config ahead
    of any postCreateCommand that runs pixi, so 'pixi install' places the
    project environment under pixi's own cache dir (already mounted as a
    volume by these templates for package caching) instead of under
    <project>/.pixi/envs - i.e. under workspaceMount's bind-mounted host
    directory, where at least Podman's WSL2 machine on Windows can't set
    file permissions/timestamps, which pixi's package-linking step needs.
    Only touches postCreateCommand when it actually mentions pixi - a
    non-pixi command is left untouched.

    Written to ~/.config/pixi/config.toml specifically - not ~/.pixi/config.toml,
    which despite some docs isn't actually in pixi's config search path
    (verified via `pixi info -vvv`'s "Loading config from" lines).
    """
    command = config.get("postCreateCommand")
    if command is None:
        return
    if isinstance(command, str):
        if "pixi" not in command:
            return
        config["postCreateCommand"] = f"{_PIXI_DETACHED_ENVIRONMENTS_STEP} && {command}"
    elif isinstance(command, list):
        if not any("pixi" in step for step in command):
            return
        config["postCreateCommand"] = [_PIXI_DETACHED_ENVIRONMENTS_STEP, *command]


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
    """Scaffold a new project's devcontainer.json from a cached template."""
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

    _prepend_pixi_detached_environments(config)

    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")
    console.print(f"Scaffolded {target} from template '{template}'.")

    _scaffold_pixi_toml(path, config["name"])


@app.command("add-feature")
def add_feature(
    name: str = typer.Argument(  # noqa: B008
        ..., help="Cached template name to merge in."
    ),
) -> None:
    """Layer a cached template's devcontainer.json fields onto the project's existing config."""
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
