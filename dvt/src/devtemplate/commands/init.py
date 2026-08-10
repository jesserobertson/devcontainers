from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape

from devtemplate.sidecar import write_sidecar

console = Console()

DEFAULT_IMAGE = "ghcr.io/jesserobertson/base-ubuntu:latest"

_PIXI_DETACHED_ENVIRONMENTS_STEP = (
    "mkdir -p ~/.config/pixi && "
    "printf 'detached-environments = true\\n' >> ~/.config/pixi/config.toml"
)
_POST_CREATE_COMMAND = f"{_PIXI_DETACHED_ENVIRONMENTS_STEP} && pixi install"

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
    table). Every feature's postCreateCommand runs 'pixi install', which
    fails outright with no manifest to install from - so a project scaffolded
    from nothing needs at least this much to make `dvt up` work end to end.
    """
    if (path / "pixi.toml").exists() or (path / "pyproject.toml").exists():
        return
    (path / "pixi.toml").write_text(_MINIMAL_PIXI_TOML.format(name=name))


def init(
    path: Path = typer.Argument(..., help="Project directory to scaffold."),  # noqa: B008
    image: str = typer.Option(  # noqa: B008
        DEFAULT_IMAGE, help=f"Base image (default: {DEFAULT_IMAGE})."
    ),
) -> None:
    """Scaffold a new project's devcontainer.json with no features yet."""
    name = path.resolve().name

    devcontainer_dir = path / ".devcontainer"
    target = devcontainer_dir / "devcontainer.json"
    if target.exists():
        console.print(
            f"[red]{escape(str(target))} already exists.[/red] "
            "Use 'dvt feature add' to layer onto it instead."
        )
        raise typer.Exit(code=1)

    config: dict[str, Any] = {
        "name": name,
        "image": image,
        "workspaceFolder": "/workspace",
        "workspaceMount": (
            "source=${localWorkspaceFolder},"
            "target=/workspace,type=bind,consistency=cached"
        ),
        "remoteUser": "dev",
        "postCreateCommand": _POST_CREATE_COMMAND,
    }

    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")
    console.print(f"Scaffolded {target}.")

    sidecar_result = write_sidecar(devcontainer_dir, {"init": config, "applied": []})
    if sidecar_result.is_err():
        console.print(
            "[yellow]Warning: failed to write the feature-tracking sidecar: "
            f"{escape(str(sidecar_result.unwrap_err()))}[/yellow]"
        )

    _scaffold_pixi_toml(path, name)
